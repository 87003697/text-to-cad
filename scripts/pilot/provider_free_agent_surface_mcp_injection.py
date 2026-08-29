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
EVIDENCE_SCHEMA = "text-to-cad.provider-free-agent-surface-mcp-injection-evidence/9"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
MAX_TOOL_NAMES = 64
MAX_TOOL_NAME_BYTES = 256
MAX_RECEIVER_REQUESTS = 2
MAX_PROTOCOL_SESSIONS = 2
MAX_PROTOCOL_METHODS = 12
MAX_CHRONOLOGY_EVENTS = 32
_FIXTURE = Path("models/toys4k/cup_cup_033.ply")
_TOOL = "inspect_formal_preview"
_VERSION = re.compile(r"(?:codex-cli|OpenAI Codex v)\s*([0-9]+\.[0-9]+\.[0-9]+)")
EXPECTED_CODEX_VERSION = "0.149.1"


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


_EXEC_INPUT = r"""const wanted = "mcp__agent_surface__inspect_formal_preview";
const alias_found = ALL_TOOLS.some(({ name }) => name === wanted);
const static_callable = typeof tools.mcp__agent_surface__inspect_formal_preview === "function";
let call_attempted = false;
let call_threw = false;
let result = null;
if (alias_found && static_callable) {
  call_attempted = true;
  try {
    result = await tools.mcp__agent_surface__inspect_formal_preview({preview_handle:"preview-probe"});
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
  first_text_classification,
  ...(() => {
    if (result?.isError !== true || typeof first_text !== "string") return {sanitized_error: null, sanitized_error_truncated: false};
    let value = first_text.replace(/[\x00-\x1f\x7f]/g, " ");
    value = value.replace(/https?:\/\/[^\s]+/gi, "<url>");
    value = value.replace(/(?<![A-Za-z0-9_.-])\/(?:[^\s\/"',}\]]+(?:\/[^\s\/"',}\]]*)*)/g, "<path>");
    value = value.replace(/\b(bearer)\s+["']?[^\s"']+["']?/gi, "$1 <redacted>");
    value = value.replace(/(["']?(?:token|secret|api[_-]?key|password|authorization)["']?\s*[:=]\s*)["']?[^\s,"'}\]]+["']?/gi, "$1<redacted>");
    value = value.replace(/\s+/g, " ").trim();
    const truncated = value.length > 256;
    return {sanitized_error: value.slice(0, 256), sanitized_error_truncated: truncated};
  })()
}));
"""


class _Chronology:
    """Thread-safe, bounded workload-only callback chronology."""

    _ALLOWED = {
        "bridge_open", "initialize", "initialized", "list", "call", "bridge_close",
        "request1_received", "exec_response_sent", "request2_received", "final_response_sent",
    }

    def __init__(self) -> None:
        self._events: list[str] = []
        self._truncated = False
        self._lock = threading.Lock()

    def record(self, event: str) -> None:
        if event not in self._ALLOWED:
            raise ValueError("invalid chronology event")
        with self._lock:
            if len(self._events) < MAX_CHRONOLOGY_EVENTS:
                self._events.append(event)
            else:
                self._truncated = True

    def projection(self) -> dict[str, object]:
        with self._lock:
            return {"events": list(self._events), "truncated": self._truncated}


class _Receiver(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, Any, str]] = []
    chronology: _Chronology | None = None

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        self.__class__.requests.append((self.path, body, self.client_address[0]))
        request_count = len(self.__class__.requests)
        if self.__class__.chronology is not None:
            if request_count == 1:
                self.__class__.chronology.record("request1_received")
            elif request_count == 2:
                self.__class__.chronology.record("request2_received")
        sent_event: str | None = None
        if request_count == 1:
            response = _response_events(call={"name": "exec", "input": _EXEC_INPUT})
            sent_event = "exec_response_sent"
        elif request_count == 2:
            response = _response_events()
            sent_event = "final_response_sent"
        else:
            response = b'{"error":{"message":"too many provider-free requests","type":"invalid_request_error"}}'
        self.send_response(200 if request_count <= 2 else 400)
        self.send_header("Content-Type", "text/event-stream" if request_count <= 2 else "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()
        if sent_event is not None and self.__class__.chronology is not None:
            self.__class__.chronology.record(sent_event)

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
        "descriptor_annotations": {},
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
    descriptor = (
        tools[0]
        if names == [_TOOL]
        and isinstance(tools, list)
        and len(tools) == 1
        and isinstance(tools[0], dict)
        else None
    )
    annotations = descriptor.get("annotations") if isinstance(descriptor, dict) else None
    if (
        isinstance(annotations, dict)
        and set(annotations)
        == {"readOnlyHint", "destructiveHint", "openWorldHint"}
        and all(type(annotations[key]) is bool for key in annotations)
    ):
        result["descriptor_annotations"] = dict(annotations)
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
    fields = {"alias_found", "static_callable", "call_attempted", "call_threw", "isError", "content_types", "structured_content_present", "supervisor_unavailable", "first_text_classification", "sanitized_error", "sanitized_error_truncated"}
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
    if type(signal["first_text_classification"]) is not str or signal["first_text_classification"] not in {"not_available_to_model", "mcp_client_not_initialized", "mcp_client_shutdown", "transport", "tool_not_found", "other", "none"}:
        return result
    if signal["sanitized_error"] is not None and (type(signal["sanitized_error"]) is not str or len(signal["sanitized_error"]) > 256):
        return result
    if type(signal["sanitized_error_truncated"]) is not bool:
        return result
    if signal["isError"] is not True and (signal["sanitized_error"] is not None or signal["sanitized_error_truncated"] is not False):
        return result
    if not isinstance(signal["content_types"], list) or len(signal["content_types"]) > 4 or any(type(value) is not str or len(value) > 64 for value in signal["content_types"]):
        return result
    result.update({"output_shape": "content_array", "script_status": status_match.group(1), **signal})
    return result


class _AuditedBridge(AgentSurfaceBridge):
    """Real bridge dispatch with a bounded record of MCP tool calls."""

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


class _NestedAuditedBridge(_AuditedBridge):
    """Add the nested-only envelope shape without changing shared audit records."""

    def _mcp_frame(self, request: dict[str, object], state: str):
        if request.get("method") == "tools/call" and len(self.audit) < 2:
            params = request.get("params")
            arguments = params.get("arguments") if isinstance(params, dict) else None
            param_keys = set(params) if isinstance(params, dict) else set()
            self.audit.append({
                "tools_call": True,
                "method": "tools/call",
                "tool_name": params.get("name") if isinstance(params, dict) else None,
                "argument_keys": sorted(arguments) if isinstance(arguments, dict) and all(type(key) is str and len(key) <= 64 for key in arguments) else [],
                "params_shape": "name_arguments" if param_keys == {"name", "arguments"} else "name_arguments_meta" if param_keys == {"name", "arguments", "_meta"} else "other",
            })
        return AgentSurfaceBridge._mcp_frame(self, request, state)


def _protocol_method(request: Mapping[str, object]) -> str:
    method = request.get("method")
    if method == "initialize":
        return "initialize"
    if method == "notifications/initialized":
        return "notifications_initialized"
    if method == "tools/list":
        return "tools_list"
    if method == "tools/call":
        return "tools_call"
    if method == "ping":
        return "ping"
    return "other_notification" if "id" not in request else "other_request"


def _saturated_count(value: int) -> str:
    return "0" if value == 0 else "1" if value == 1 else "2_plus"


class _ProtocolAuditedBridge(_NestedAuditedBridge):
    """A closed, per-connection MCP protocol projection for the nested gate."""

    def __init__(self, surface: object, socket_path: Path, *, chronology: _Chronology | None = None) -> None:
        super().__init__(surface, socket_path)
        self._chronology = chronology
        self._sessions: list[dict[str, object]] = []
        self._sessions_lock = threading.Lock()
        self._local = threading.local()
        self._sessions_truncated = False

    def _serve_connection(self, connection: socket.socket) -> None:
        session: dict[str, object] | None
        with self._sessions_lock:
            if len(self._sessions) >= MAX_PROTOCOL_SESSIONS:
                self._sessions_truncated = True
                session = None
            else:
                session = {"methods": [], "truncated": False, "initialize_ok": False,
                           "initialized": False, "list_ok": False, "call_seen": False,
                           "state": "pre_init", "closed": False}
                self._sessions.append(session)
        self._local.session = session
        if self._chronology is not None:
            self._chronology.record("bridge_open")
        try:
            super()._serve_connection(connection)
        finally:
            if session is not None:
                session["closed"] = True
            if self._chronology is not None:
                self._chronology.record("bridge_close")
            self._local.session = None

    def _mcp_frame(self, request: dict[str, object], state: str):
        session = getattr(self._local, "session", None)
        method = _protocol_method(request)
        if self._chronology is not None:
            event = {"initialize": "initialize", "notifications_initialized": "initialized", "tools_list": "list", "tools_call": "call"}.get(method)
            if event is not None:
                self._chronology.record(event)
        if isinstance(session, dict):
            methods = session["methods"]
            if isinstance(methods, list):
                if len(methods) < MAX_PROTOCOL_METHODS:
                    methods.append(method)
                else:
                    session["truncated"] = True
        frame, next_state = super()._mcp_frame(request, state)
        if isinstance(session, dict):
            if method == "initialize":
                session["initialize_ok"] = isinstance(frame, dict) and isinstance(frame.get("result"), dict)
            elif method == "notifications_initialized" and state == self._mcp.NEGOTIATED and next_state == self._mcp.ACTIVE:
                session["initialized"] = True
            elif method == "tools_list":
                tools = frame.get("result", {}).get("tools") if isinstance(frame, dict) and isinstance(frame.get("result"), dict) else None
                session["list_ok"] = isinstance(tools, list)
            elif method == "tools_call":
                session["call_seen"] = True
            session["state"] = "active" if next_state == self._mcp.ACTIVE else "negotiated" if next_state == self._mcp.NEGOTIATED else "pre_init"
        return frame, next_state

    def protocol_projection(self) -> dict[str, object]:
        with self._sessions_lock:
            sessions = [dict(session) for session in self._sessions]
            truncated = self._sessions_truncated
        projected: list[dict[str, object]] = []
        for session in sessions:
            methods = session["methods"]
            assert isinstance(methods, list)
            projected.append({
                "method_count": _saturated_count(len(methods)),
                "methods": list(methods),
                "state": session["state"],
                "closed": session["closed"],
                "list_succeeded": session["list_ok"],
                "call_seen": session["call_seen"],
                "truncated": session["truncated"],
                "initialize_succeeded": session["initialize_ok"],
                "initialized": session["initialized"],
            })
        count = _saturated_count(len(sessions))
        if truncated or count == "2_plus" or any(session["truncated"] for session in projected):
            classification = "multiple_sessions"
        elif count == "0":
            classification = "no_bridge_connection"
        else:
            session = projected[0]
            if not session["initialize_succeeded"]:
                classification = "connected_without_initialize" if "initialize" not in session["methods"] else "initialize_rejected_or_incomplete"
            elif not session["initialized"]:
                classification = "negotiated_without_initialized"
            elif not session["list_succeeded"]:
                classification = "active_without_list"
            elif session["call_seen"]:
                classification = "live_call_reached_bridge"
            else:
                classification = "live_listed_no_call"
        return {"session_count": count, "sessions": projected, "truncated": truncated, "classification": classification}



def _valid_protocol_audit(value: object) -> bool:
    fields = {"session_count", "sessions", "truncated", "classification"}
    classes = {"live_call_reached_bridge", "live_listed_no_call", "active_without_list", "negotiated_without_initialized", "initialize_rejected_or_incomplete", "connected_without_initialize", "no_bridge_connection", "multiple_sessions"}
    if not isinstance(value, dict) or set(value) != fields:
        return False
    if type(value.get("session_count")) is not str or value["session_count"] not in {"0", "1", "2_plus"} or type(value.get("truncated")) is not bool or type(value.get("classification")) is not str or value["classification"] not in classes:
        return False
    sessions = value["sessions"]
    if not isinstance(sessions, list) or len(sessions) > MAX_PROTOCOL_SESSIONS or _saturated_count(len(sessions)) != value["session_count"]:
        return False
    session_fields = {"method_count", "methods", "state", "closed", "list_succeeded", "call_seen", "truncated", "initialize_succeeded", "initialized"}
    allowed_methods = {"initialize", "notifications_initialized", "tools_list", "tools_call", "ping", "other_request", "other_notification"}
    classifications: list[str] = []
    for session in sessions:
        if not isinstance(session, dict) or set(session) != session_fields:
            return False
        methods = session["methods"]
        if not isinstance(methods, list) or len(methods) > MAX_PROTOCOL_METHODS or any(type(method) is not str or method not in allowed_methods for method in methods):
            return False
        if type(session["method_count"]) is not str or session["method_count"] != _saturated_count(len(methods)) or type(session["state"]) is not str or session["state"] not in {"pre_init", "negotiated", "active"}:
            return False
        if any(type(session[key]) is not bool for key in ("closed", "list_succeeded", "call_seen", "truncated", "initialize_succeeded", "initialized")):
            return False
        if session["truncated"] or not session["closed"]:
            return False
        # The successful path is intentionally exact.  A ping or unknown frame
        # may be diagnostic evidence but cannot establish a successful gate.
        if methods == []:
            expected = ("connected_without_initialize", "pre_init", False, False, False, False)
        elif methods == ["initialize"] and session["state"] == "pre_init":
            expected = ("initialize_rejected_or_incomplete", "pre_init", False, False, False, False)
        elif methods == ["initialize"]:
            expected = ("negotiated_without_initialized", "negotiated", True, False, False, False)
        elif methods == ["initialize", "notifications_initialized"]:
            expected = ("active_without_list", "active", True, True, False, False)
        elif methods == ["initialize", "notifications_initialized", "tools_list"]:
            expected = ("live_listed_no_call", "active", True, True, True, False)
        elif methods == ["initialize", "notifications_initialized", "tools_list", "tools_call"]:
            expected = ("live_call_reached_bridge", "active", True, True, True, True)
        else:
            return False
        classification, state, initialize_ok, initialized, list_ok, call_seen = expected
        if session["state"] != state or session["initialize_succeeded"] is not initialize_ok or session["initialized"] is not initialized or session["list_succeeded"] is not list_ok or session["call_seen"] is not call_seen:
            return False
        classifications.append(classification)
    if value["truncated"] or value["session_count"] == "2_plus":
        expected_classification = "multiple_sessions"
    elif value["session_count"] == "0":
        expected_classification = "no_bridge_connection"
    else:
        expected_classification = classifications[0]
    return value["classification"] == expected_classification


def _valid_chronology(value: object) -> bool:
    expected = {
        "request1_received", "exec_response_sent", "bridge_open", "initialize",
        "initialized", "list", "call", "bridge_close", "request2_received",
        "final_response_sent",
    }
    if not isinstance(value, dict) or set(value) != {"events", "truncated"} or type(value.get("truncated")) is not bool or value["truncated"] is not False:
        return False
    events = value["events"]
    if not isinstance(events, list) or len(events) != len(expected) or any(type(event) is not str for event in events) or set(events) != expected or len(events) != len(set(events)):
        return False
    positions = {event: index for index, event in enumerate(events)}
    return (
        positions["request1_received"] < positions["exec_response_sent"]
        and positions["bridge_open"] < positions["initialize"] < positions["initialized"] < positions["list"] < positions["call"]
        and positions["call"] < positions["request2_received"] < positions["final_response_sent"]
        and positions["call"] < positions["bridge_close"]
    )


def _exact_preflight_protocol(value: object) -> bool:
    return _valid_protocol_audit(value) and value == {
        "session_count": "1",
        "sessions": [{
            "method_count": "2_plus",
            "methods": ["initialize", "notifications_initialized", "tools_list", "tools_call"],
            "state": "active",
            "closed": True,
            "list_succeeded": True,
            "call_seen": True,
            "truncated": False,
            "initialize_succeeded": True,
            "initialized": True,
        }],
        "truncated": False,
        "classification": "live_call_reached_bridge",
    }

def _valid_dispatch(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("audit_count"), str):
        return False
    count = value["audit_count"]
    if count == "0":
        return set(value) == {"audit_count"}
    fields = {"audit_count", "tools_call", "method", "tool_name", "argument_keys", "params_shape"}
    if type(count) is not str or count not in {"1", "2_plus"} or set(value) != fields:
        return False
    if value["tools_call"] is not True:
        return False
    if type(value["method"]) is not str or len(value["method"]) > 64:
        return False
    if value["tool_name"] is not None and (type(value["tool_name"]) is not str or len(value["tool_name"]) > MAX_TOOL_NAME_BYTES):
        return False
    if type(value["params_shape"]) is not str or value["params_shape"] not in {"name_arguments", "name_arguments_meta", "other"}:
        return False
    keys = value["argument_keys"]
    return isinstance(keys, list) and len(keys) <= 16 and all(type(key) is str and len(key) <= 64 for key in keys)


def _valid_second_request(value: object) -> bool:
    base = {"call_id_matches", "output_shape", "script_status"}
    if not isinstance(value, dict) or not base <= set(value):
        return False
    if type(value["call_id_matches"]) is not bool or type(value["output_shape"]) is not str or value["output_shape"] not in {"invalid", "content_array"} or type(value["script_status"]) is not str or value["script_status"] not in {"unknown", "completed", "failed"}:
        return False
    if value["output_shape"] == "invalid":
        return set(value) == base and value["script_status"] == "unknown"
    fields = base | {"alias_found", "static_callable", "call_attempted", "call_threw", "isError", "content_types", "structured_content_present", "supervisor_unavailable", "first_text_classification", "sanitized_error", "sanitized_error_truncated"}
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
    if type(value["first_text_classification"]) is not str or value["first_text_classification"] not in {"not_available_to_model", "mcp_client_not_initialized", "mcp_client_shutdown", "transport", "tool_not_found", "other", "none"}:
        return False
    if value["sanitized_error"] is not None and (type(value["sanitized_error"]) is not str or len(value["sanitized_error"]) > 256):
        return False
    if type(value["sanitized_error_truncated"]) is not bool:
        return False
    if value["isError"] is not True and (value["sanitized_error"] is not None or value["sanitized_error_truncated"] is not False):
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
    fields = {"schema", "identity", "sandbox", "private_config", "mcp_preflight", "preflight_dispatch", "preflight_protocol", "first_request", "workload_protocol", "workload_chronology", "mcp_dispatch", "second_request", "receiver", "process", "gate_passed"}
    if not isinstance(evidence, dict) or set(evidence) != fields or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != identity:
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    if evidence.get("sandbox") != {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}:
        raise ProviderFreeError("provider-free evidence has an invalid sandbox contract")
    if evidence.get("private_config") != {"agent_surface_server_enabled": True}:
        raise ProviderFreeError("provider-free evidence has an invalid config result")
    expected_preflight = {
        "spawned": True,
        "initialize_ok": True,
        "tools_list_ok": True,
        "exact_descriptor_seen": True,
        "descriptor_annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        "tools_call_ok": True,
        "is_error": True,
        "text_present": True,
        "supervisor_unavailable": True,
        "exit_class": "zero",
    }
    if evidence.get("mcp_preflight") != expected_preflight:
        raise ProviderFreeError("provider-free evidence has an invalid MCP preflight")
    preflight_dispatch = evidence.get("preflight_dispatch")
    if not _valid_dispatch(preflight_dispatch) or preflight_dispatch != {"audit_count": "1", "tools_call": True, "method": "tools/call", "tool_name": _TOOL, "argument_keys": ["preview_handle"], "params_shape": "name_arguments"}:
        raise ProviderFreeError("provider-free evidence has an invalid preflight dispatch")
    if not _exact_preflight_protocol(evidence.get("preflight_protocol")):
        raise ProviderFreeError("provider-free evidence has an invalid preflight protocol")
    first = evidence.get("first_request")
    if not isinstance(first, dict) or set(first) != {"received", "protocol"} or first["received"] is not True or not _valid_protocol(first["protocol"]):
        raise ProviderFreeError("provider-free evidence has an invalid first request")
    dispatch = evidence.get("mcp_dispatch")
    if not _valid_dispatch(dispatch):
        raise ProviderFreeError("provider-free evidence has an invalid MCP dispatch")
    if dispatch != {"audit_count": "1", "tools_call": True, "method": "tools/call", "tool_name": _TOOL, "argument_keys": ["preview_handle"], "params_shape": "name_arguments_meta"}:
        raise ProviderFreeError("provider-free evidence has an unsuccessful MCP dispatch")
    workload_protocol = evidence.get("workload_protocol")
    if not _valid_chronology(evidence.get("workload_chronology")):
        raise ProviderFreeError("provider-free evidence has an invalid workload chronology")
    if not _valid_protocol_audit(workload_protocol) or workload_protocol.get("classification") != "live_call_reached_bridge" or workload_protocol.get("truncated") is not False or workload_protocol.get("session_count") != "1":
        raise ProviderFreeError("provider-free evidence has an invalid workload bridge protocol")
    session = workload_protocol["sessions"][0]
    if session["call_seen"] is not True or session["list_succeeded"] is not True:
        raise ProviderFreeError("provider-free evidence has an inconsistent workload protocol")
    second = evidence.get("second_request")
    if not _valid_second_request(second):
        raise ProviderFreeError("provider-free evidence has an invalid second request")
    expected_signal = {"call_id_matches": True, "output_shape": "content_array", "script_status": "completed", "alias_found": True, "static_callable": True, "call_attempted": True, "call_threw": False, "isError": True, "content_types": ["text"], "structured_content_present": True, "supervisor_unavailable": True, "first_text_classification": "other"}
    if not all(second.get(key) == expected for key, expected in expected_signal.items()):
        raise ProviderFreeError("provider-free evidence has an unsuccessful second request")
    if evidence.get("receiver") != {"loopback_only": True, "provider_escape": False, "request_count": 2}:
        raise ProviderFreeError("provider-free evidence has an invalid receiver result")
    process = evidence.get("process")
    if not isinstance(process, dict) or set(process) != {"codex_version", "version_exit_code", "workload_exit_code"} or process["codex_version"] != EXPECTED_CODEX_VERSION or type(process["version_exit_code"]) is not int or process["version_exit_code"] != 0 or type(process["workload_exit_code"]) is not int or process["workload_exit_code"] != 0 or evidence.get("gate_passed") is not True:
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
    preflight_socket_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-preflight-socket-"))
    workload_socket_dir = Path(tempfile.mkdtemp(prefix="ttc-agent-surface-workload-socket-"))
    preflight_bridge: _ProtocolAuditedBridge | None = None
    workload_bridge: _ProtocolAuditedBridge | None = None
    server: http.server.ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    version_exit = workload_exit = None
    version = None
    preflight = {
        "spawned": False,
        "initialize_ok": False,
        "tools_list_ok": False,
        "exact_descriptor_seen": False,
        "descriptor_annotations": {},
        "tools_call_ok": False,
        "is_error": None,
        "text_present": False,
        "supervisor_unavailable": None,
        "exit_class": "spawn_failed",
    }
    preflight_dispatch: dict[str, object] = {"audit_count": "0"}
    preflight_protocol: dict[str, object] = {"session_count": "0", "sessions": [], "truncated": False, "classification": "no_bridge_connection"}
    workload_protocol: dict[str, object] = {"session_count": "0", "sessions": [], "truncated": False, "classification": "no_bridge_connection"}
    workload_audit: list[dict[str, object]] = []
    workload_chronology = _Chronology()
    config_enabled = False
    requests: list[tuple[str, Any, str]] = []
    try:
        config_path = runner.prepare_isolated_job_codex_home(exp_dir) / plugin_deployment.CONFIG_TOML_NAME
        config_enabled = _config_enabled(config_path)
        gate_env = dict(environ)
        gate_env["VENUS_TOKEN"] = "provider-free-loopback-only"
        fixture = repo_root / _FIXTURE
        child_env = runner.build_sandbox_environment(gate_env, "http://127.0.0.1:9/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")

        preflight_bridge = _ProtocolAuditedBridge(None, preflight_socket_dir / "surface.sock")
        preflight_bridge.surface = preflight_bridge._mcp.AgentSurface(None)
        preflight_bridge.start()
        try:
            preflight_argv = runner.build_bwrap_argv(repo_root, exp_dir, [fixture], ["python3", "/agent-surface/client.py", "--mcp"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=preflight_bridge.socket_path)
            preflight = _same_sandbox_mcp_preflight(preflight_argv, child_env)
        finally:
            preflight_bridge.stop()
        preflight_dispatch = _audit_projection(preflight_bridge.audit)
        preflight_protocol = preflight_bridge.protocol_projection()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        _Receiver.requests = []
        _Receiver.chronology = workload_chronology
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        child_env = runner.build_sandbox_environment(gate_env, f"http://127.0.0.1:{server.server_port}/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")
        workload_bridge = _ProtocolAuditedBridge(None, workload_socket_dir / "surface.sock", chronology=workload_chronology)
        workload_bridge.surface = workload_bridge._mcp.AgentSurface(None)
        workload_bridge.start()
        try:
            version_result = subprocess.run(runner.build_bwrap_argv(repo_root, exp_dir, [fixture], ["codex", "--version"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=workload_bridge.socket_path), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30)
            version_exit = _safe_returncode(version_result.returncode)
            version = _version_from_output(version_result.stdout + "\n" + version_result.stderr)
            workload = ["gateway/codex-tap-gpt56", "sol", "exec", "--skip-git-repo-check", "--ephemeral", "run the fixed provider-free MCP canary"]
            workload_result = subprocess.run(runner.build_bwrap_argv(repo_root, exp_dir, [fixture], workload, gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=workload_bridge.socket_path), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=60)
            workload_exit = _safe_returncode(workload_result.returncode)
            requests = list(_Receiver.requests)
        finally:
            workload_bridge.stop()
        workload_audit = list(workload_bridge.audit)
        workload_protocol = workload_bridge.protocol_projection()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        _Receiver.chronology = None
        if preflight_bridge is not None and preflight_bridge._server is not None:
            preflight_bridge.stop()
        if workload_bridge is not None and workload_bridge._server is not None:
            workload_bridge.stop()
        shutil.rmtree(candidate_dir, ignore_errors=True)
        shutil.rmtree(preflight_socket_dir, ignore_errors=True)
        shutil.rmtree(workload_socket_dir, ignore_errors=True)
        shutil.rmtree(exp_dir / "run" / ".codex-home", ignore_errors=True)
    first_body = requests[0][1] if requests else None
    second_body = requests[1][1] if len(requests) > 1 else None
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "identity": identity,
        "sandbox": {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"},
        "private_config": {"agent_surface_server_enabled": config_enabled},
        "mcp_preflight": preflight,
        "preflight_dispatch": preflight_dispatch,
        "preflight_protocol": preflight_protocol,
        "first_request": {"received": bool(requests), "protocol": _protocol_projection(first_body)},
        "workload_protocol": workload_protocol,
        "workload_chronology": workload_chronology.projection(),
        "mcp_dispatch": _audit_projection(workload_audit),
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
