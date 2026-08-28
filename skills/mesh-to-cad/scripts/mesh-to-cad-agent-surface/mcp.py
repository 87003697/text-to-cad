"""Minimal newline JSON-RPC/MCP adapter sharing the Agent Surface handler."""

from __future__ import annotations

import base64
import json
import math
import sys
from typing import TextIO

from handler import (
    MAX_REQUEST_BYTES,
    AgentSurface,
    AgentSurfaceError,
    REQUEST_SCHEMA,
    error_document,
    tool_descriptors,
)

JSON_RPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
NOT_INITIALIZED = -32002
PRE_INIT = "pre-init"
NEGOTIATED = "negotiated"
ACTIVE = "active"
MCP_INTENT = "inspect_formal_preview"


def _rpc_error(request_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": JSON_RPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_result(
    request_id,
    payload: dict,
    *,
    preview_png: bytes | None = None,
    is_error: bool = False,
) -> dict:
    content = [
        {
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        }
    ]
    if type(preview_png) is bytes:
        content.append(
            {
                "type": "image",
                "data": base64.b64encode(preview_png).decode("ascii"),
                "mimeType": "image/png",
            }
        )
    return {
        "jsonrpc": JSON_RPC_VERSION,
        "id": request_id,
        "result": {
            "isError": is_error,
            "structuredContent": payload,
            "content": content,
        },
    }


def _string_param(value) -> bool:
    return type(value) is str and bool(value) and len(value) <= 256


def _bounded_json_value(value, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if value is None or type(value) in {bool, int}:
        return True
    if type(value) is float:
        return value == value and value not in {float("inf"), float("-inf")}
    if type(value) is str:
        return len(value) <= 256
    if type(value) is list:
        return len(value) <= 16 and all(_bounded_json_value(item, depth + 1) for item in value)
    if type(value) is dict:
        return (
            len(value) <= 16
            and all(type(key) is str and len(key) <= 64 for key in value)
            and all(_bounded_json_value(item, depth + 1) for item in value.values())
        )
    return False


def _initialize_result(request: dict, state: str) -> tuple[dict | None, str]:
    request_id = request.get("id")
    if state != PRE_INIT:
        return _rpc_error(request_id, INVALID_REQUEST, "server is already initialized"), state
    params = request.get("params")
    if type(params) is not dict or set(params) != {"protocolVersion", "capabilities", "clientInfo"}:
        return _rpc_error(request_id, INVALID_PARAMS, "initialize parameters are invalid"), state
    if not _string_param(params["protocolVersion"]):
        return _rpc_error(request_id, INVALID_PARAMS, "protocol version is invalid"), state
    capabilities = params["capabilities"]
    if type(capabilities) is not dict or not _bounded_json_value(capabilities):
        return _rpc_error(request_id, INVALID_PARAMS, "client capabilities are invalid"), state
    if any(
        (key in {"roots", "sampling", "elicitation"} and type(value) is not dict)
        for key, value in capabilities.items()
    ):
        return _rpc_error(request_id, INVALID_PARAMS, "client capabilities are invalid"), state
    experimental = capabilities.get("experimental")
    if experimental is not None and (
        type(experimental) is not dict
        or any(type(value) is not dict for value in experimental.values())
    ):
        return _rpc_error(request_id, INVALID_PARAMS, "experimental capabilities are invalid"), state
    client_info = params["clientInfo"]
    if (
        type(client_info) is not dict
        or set(client_info) - {"name", "version", "title"}
        or not {"name", "version"}.issubset(client_info)
        or not _string_param(client_info["name"])
        or not _string_param(client_info["version"])
        or ("title" in client_info and not _string_param(client_info["title"]))
    ):
        return _rpc_error(request_id, INVALID_PARAMS, "clientInfo is invalid"), state
    return {
        "jsonrpc": JSON_RPC_VERSION,
        "id": request_id,
        "result": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "mesh-to-cad-agent-surface",
                "version": "1",
            },
        },
    }, NEGOTIATED


def _valid_tools_list_params(params) -> bool:
    if params is None or (type(params) is dict and not params):
        return True
    if type(params) is not dict or set(params) != {"_meta"}:
        return False
    meta = params["_meta"]
    return (
        type(meta) is dict
        and set(meta) == {"progressToken"}
        and _valid_request_id(meta["progressToken"])
    )


def _valid_initialized_notification(request: dict) -> bool:
    params = request.get("params", {})
    return (
        set(request) <= {"jsonrpc", "method", "params"}
        and request.get("jsonrpc") == JSON_RPC_VERSION
        and request.get("method") == "notifications/initialized"
        and type(params) is dict
        and _bounded_json_value(params)
    )


def _valid_request_id(value) -> bool:
    if value is None or type(value) is str:
        return value is None or len(value) <= 256
    if type(value) is int and not isinstance(value, bool):
        return True
    return type(value) is float and math.isfinite(value)


def _handle_request(
    handler: AgentSurface,
    request: dict,
    state: str,
) -> tuple[dict | None, str]:
    # JSON-RPC notifications are identified by the absence of ``id`` before
    # method dispatch.  Future/unknown notifications are intentionally silent.
    if "id" not in request:
        if state == NEGOTIATED and _valid_initialized_notification(request):
            return None, ACTIVE
        return None, state
    request_id = request.get("id")
    method = request.get("method")
    if type(method) is not str or set(request) - {"jsonrpc", "id", "method", "params"}:
        return _rpc_error(request_id, INVALID_REQUEST, "request is invalid"), state
    if method == "initialize":
        return _initialize_result(request, state)
    if state != ACTIVE:
        return _rpc_error(request_id, NOT_INITIALIZED, "server is not active"), state
    if method == "tools/list":
        if not _valid_tools_list_params(request.get("params")):
            return _rpc_error(request_id, INVALID_PARAMS, "tools/list parameters are invalid"), state
        descriptors = [
            descriptor
            for descriptor in tool_descriptors()
            if descriptor["name"] == MCP_INTENT
        ]
        return {
            "jsonrpc": JSON_RPC_VERSION,
            "id": request_id,
            "result": {"tools": descriptors},
        }, state
    if method != "tools/call":
        return _rpc_error(request_id, METHOD_NOT_FOUND, "method is not supported"), state
    params = request.get("params")
    if type(params) is not dict or set(params) != {"name", "arguments"}:
        return _rpc_error(request_id, INVALID_PARAMS, "tool parameters are invalid"), state
    name = params["name"]
    arguments = params["arguments"]
    if type(name) is not str or name != MCP_INTENT or type(arguments) is not dict:
        return _rpc_error(request_id, INVALID_PARAMS, "tool parameters are invalid"), state
    try:
        payload, preview_png = handler.handle_mcp(
            {"schema": REQUEST_SCHEMA, "intent": name, "args": arguments}
        )
    except AgentSurfaceError as error:
        return _tool_result(request_id, error_document(error), is_error=True), state
    return _tool_result(request_id, payload, preview_png=preview_png), state


def serve(
    ports=None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC requests until EOF."""

    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    handler = AgentSurface(ports)
    state = PRE_INIT
    while True:
        line = input_stream.readline(MAX_REQUEST_BYTES + 1)
        if line == "":
            break
        if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
            frame = _rpc_error(None, INVALID_REQUEST, "request is too large")
            output_stream.write(json.dumps(frame, ensure_ascii=True, separators=(",", ":")))
            output_stream.write("\n")
            output_stream.flush()
            return 2
        else:
            try:
                request = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                frame = _rpc_error(None, PARSE_ERROR, "invalid JSON")
            else:
                if type(request) is not dict:
                    frame = _rpc_error(None, INVALID_REQUEST, "request is invalid")
                elif "id" not in request:
                    frame, state = _handle_request(handler, request, state)
                elif request.get("jsonrpc") != JSON_RPC_VERSION:
                    frame = _rpc_error(None, INVALID_REQUEST, "request is invalid")
                elif "id" in request and not _valid_request_id(request["id"]):
                    frame = _rpc_error(None, INVALID_REQUEST, "request id is invalid")
                else:
                    frame, state = _handle_request(handler, request, state)
        if frame is not None:
            output_stream.write(json.dumps(frame, ensure_ascii=True, separators=(",", ":")))
            output_stream.write("\n")
            output_stream.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())


__all__ = ["serve"]
