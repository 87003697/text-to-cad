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
EVIDENCE_SCHEMA = "text-to-cad.provider-free-agent-surface-mcp-injection-evidence/1"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
MAX_TOOL_NAMES = 64
MAX_TOOL_NAME_BYTES = 256
MAX_RECEIVER_REQUESTS = 64
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


class _Receiver(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, Any, str]] = []

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

    def log_message(self, *_: object) -> None:
        pass


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
        evidence_bytes = read_bounded(evidence_path, MAX_EVIDENCE_BYTES)
        manifest = json.loads(read_bounded(manifest_path, MAX_MANIFEST_BYTES))
        evidence = json.loads(evidence_bytes)
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("provider-free evidence or artifact manifest is missing/invalid") from exc
    identity = expected_identity(record)
    expected_fields = {
        "schema", "identity", "sandbox", "private_config", "mcp_preflight",
        "first_request", "receiver", "process", "gate_passed",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_fields:
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    if evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != identity:
        raise ProviderFreeError("provider-free evidence identity differs from job")
    sandbox = evidence.get("sandbox")
    if sandbox != {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}:
        raise ProviderFreeError("provider-free evidence has an invalid sandbox contract")
    config = evidence.get("private_config")
    if not isinstance(config, dict) or set(config) != {"agent_surface_server_enabled"} or type(config["agent_surface_server_enabled"]) is not bool:
        raise ProviderFreeError("provider-free evidence has an invalid config result")
    preflight = evidence.get("mcp_preflight")
    if not isinstance(preflight, dict) or set(preflight) != {"initialize_succeeded", "tools_list_succeeded", "tool_descriptor_names"}:
        raise ProviderFreeError("provider-free evidence has an invalid MCP preflight")
    if not all(type(preflight[name]) is bool for name in ("initialize_succeeded", "tools_list_succeeded")) or preflight["tool_descriptor_names"] not in ([], [_TOOL]):
        raise ProviderFreeError("provider-free evidence has an invalid MCP preflight")
    first = evidence.get("first_request")
    if not isinstance(first, dict) or set(first) != {"received", "tool_descriptor_names", "exact_inspect_name", "qualified_inspect_name"}:
        raise ProviderFreeError("provider-free evidence has an invalid captured request")
    if type(first["received"]) is not bool or type(first["exact_inspect_name"]) is not bool or type(first["qualified_inspect_name"]) is not bool or not isinstance(first["tool_descriptor_names"], list):
        raise ProviderFreeError("provider-free evidence has an invalid captured request")
    for names in (preflight["tool_descriptor_names"], first["tool_descriptor_names"]):
        if len(names) > MAX_TOOL_NAMES or any(type(name) is not str or len(name) > MAX_TOOL_NAME_BYTES for name in names):
            raise ProviderFreeError("provider-free evidence has an invalid tool descriptor list")
    receiver = evidence.get("receiver")
    if not isinstance(receiver, dict) or set(receiver) != {"loopback_only", "provider_escape", "request_count"} or type(receiver["loopback_only"]) is not bool or receiver["provider_escape"] is not False or type(receiver["request_count"]) is not int or not 0 <= receiver["request_count"] <= MAX_RECEIVER_REQUESTS:
        raise ProviderFreeError("provider-free evidence has an invalid receiver result")
    process = evidence.get("process")
    if not isinstance(process, dict) or set(process) != {"codex_version", "version_exit_code", "workload_exit_code"}:
        raise ProviderFreeError("provider-free evidence has an invalid process result")
    if process["codex_version"] is not None and (not isinstance(process["codex_version"], str) or len(process["codex_version"]) > 32):
        raise ProviderFreeError("provider-free evidence has an invalid Codex version")
    if any(process[name] is not None and not isinstance(process[name], int) for name in ("version_exit_code", "workload_exit_code")) or type(evidence["gate_passed"]) is not bool:
        raise ProviderFreeError("provider-free evidence has an invalid gate result")
    expected_manifest = {
        "schema": MANIFEST_SCHEMA,
        "final_status": 0 if evidence["gate_passed"] else 1,
        "identity": identity,
        "evidence": {"path": evidence_path.name},
    }
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
    bridge: AgentSurfaceBridge | None = None
    server: http.server.ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    version_exit = workload_exit = None
    version = None
    config_enabled = False
    preflight = {"initialize_succeeded": False, "tools_list_succeeded": False, "tool_descriptor_names": []}
    first = None
    names: list[str] = []
    exact = qualified = loopback_only = False
    try:
        surface_dir = repo_root / "skills/mesh-to-cad/scripts/mesh-to-cad-agent-surface"
        sys.path.insert(0, os.fspath(surface_dir))
        from handler import AgentSurface
        bridge = AgentSurfaceBridge(AgentSurface(None), socket_path)
        bridge.start()
        preflight = _mcp_preflight(socket_path)
        config_path = runner.prepare_isolated_job_codex_home(exp_dir) / plugin_deployment.CONFIG_TOML_NAME
        config_enabled = _config_enabled(config_path)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        _Receiver.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gate_env = dict(environ)
        gate_env["VENUS_TOKEN"] = "provider-free-loopback-only"
        fixture = repo_root / _FIXTURE
        workload = ["gateway/codex-tap-gpt56", "sol", "exec", "--skip-git-repo-check", "--ephemeral", "capture MCP tools only"]
        child_env = runner.build_sandbox_environment(gate_env, f"http://127.0.0.1:{server.server_port}/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")
        version_argv = runner.build_bwrap_argv(repo_root, exp_dir, [fixture], ["codex", "--version"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path)
        version_result = subprocess.run(version_argv, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30)
        version_exit = _safe_returncode(version_result.returncode)
        version = _version_from_output(version_result.stdout + "\n" + version_result.stderr)
        workload_argv = runner.build_bwrap_argv(repo_root, exp_dir, [fixture], workload, gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path)
        workload_result = subprocess.run(workload_argv, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=45)
        workload_exit = _safe_returncode(workload_result.returncode)
        first = _Receiver.requests[0] if _Receiver.requests else None
        names = _bounded_names(first[1]) if first is not None else []
        exact, qualified = _inspect_name(names)
        loopback_only = bool(_Receiver.requests) and all(path == "/v1/responses" and host == "127.0.0.1" for path, _body, host in _Receiver.requests)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        if bridge is not None:
            bridge.stop()
        shutil.rmtree(candidate_dir, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
        shutil.rmtree(exp_dir / "run" / ".codex-home", ignore_errors=True)
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "identity": identity,
        "sandbox": {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"},
        "private_config": {"agent_surface_server_enabled": config_enabled},
        "mcp_preflight": preflight,
        "first_request": {"received": first is not None, "tool_descriptor_names": names, "exact_inspect_name": exact, "qualified_inspect_name": qualified},
        "receiver": {"loopback_only": loopback_only, "provider_escape": False, "request_count": len(_Receiver.requests)},
        "process": {"codex_version": version, "version_exit_code": version_exit, "workload_exit_code": workload_exit},
        "gate_passed": config_enabled and preflight == {"initialize_succeeded": True, "tools_list_succeeded": True, "tool_descriptor_names": [_TOOL]} and first is not None and (exact or qualified) and loopback_only,
    }
    _atomic_write(evidence_path, evidence)
    _atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 0 if evidence["gate_passed"] else 1, "identity": identity, "evidence": {"path": evidence_path.name}})
    validate_artifacts(repo_root, record)
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
