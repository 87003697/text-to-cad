#!/usr/bin/env python3
"""Closed provider-free differential for Codex MCP injection persistence."""
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

from scripts.pilot import plugin_deployment
from scripts.pilot import provider_free_agent_surface_mcp_injection as injection
from scripts.pilot import runner
from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge
from scripts.pilot.cvm_job import protocol

SCENARIO = "agent-surface-mcp-ephemeral-differential"
EVIDENCE_SCHEMA = "text-to-cad.provider-free-agent-surface-mcp-ephemeral-differential-evidence/1"
MANIFEST_SCHEMA = injection.MANIFEST_SCHEMA
_ARMS = (("ephemeral", True), ("persistent", False))


class ProviderFreeError(RuntimeError):
    pass


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return injection.authority_identity(receipt)


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    authority = record.get("plugin_authority")
    job = record.get("job")
    if (
        not isinstance(authority, dict)
        or set(authority) != set(injection.installed.AUTHORITY_FIELDS)
        or any(not isinstance(authority[key], str) or not authority[key] for key in authority)
        or record.get("provider_free") is not True
        or record.get("scenario") != SCENARIO
        or record.get("object") != SCENARIO
        or record.get("token_slot") is not None
        or record.get("exp_dir") != f"outputs/{job}"
    ):
        raise ProviderFreeError("job is not an ephemeral differential request")
    return {"job": job, "scenario": SCENARIO, "plugin_selector": plugin_deployment.PLUGIN_SELECTOR, "marketplace": plugin_deployment.MARKETPLACE_NAME, "authority": dict(authority)}


def assert_current_authority(record: Mapping[str, Any], host_home: Path) -> plugin_deployment.DeploymentReceipt:
    try:
        receipt = plugin_deployment.resolve_current_authority(host_home)
    except plugin_deployment.PluginAuthorityError as exc:
        raise ProviderFreeError(f"plugin authority is unavailable: {exc}") from exc
    if authority_identity(receipt) != record.get("plugin_authority"):
        raise ProviderFreeError("current plugin authority differs from submitted job")
    return receipt


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    return injection.build_runner_env(environ)


def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    exp_dir = repo_root / str(record["exp_dir"])
    return exp_dir, exp_dir / "provider-free-evidence.json", exp_dir / "artifact_manifest.json"


def _workload(ephemeral: bool) -> list[str]:
    argv = ["gateway/codex-tap-gpt56", "sol", "exec", "--skip-git-repo-check"]
    if ephemeral:
        argv.append("--ephemeral")
    return [*argv, "capture MCP tools only"]


def _run_arm(name: str, ephemeral: bool, *, repo_root: Path, exp_dir: Path, gate_env: Mapping[str, str]) -> dict[str, Any]:
    arm_dir = exp_dir / "arms" / name
    candidate_dir = Path(tempfile.mkdtemp(prefix=f"ttc-{name}-candidate-"))
    socket_dir = Path(tempfile.mkdtemp(prefix=f"ttc-{name}-surface-"))
    bridge: AgentSurfaceBridge | None = None
    server: http.server.ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    class Receiver(injection._Receiver):
        requests: list[tuple[str, Any, str]] = []
    try:
        surface_dir = repo_root / "skills/mesh-to-cad/scripts/mesh-to-cad-agent-surface"
        sys.path.insert(0, os.fspath(surface_dir))
        from handler import AgentSurface
        socket_path = socket_dir / "surface.sock"
        bridge = AgentSurfaceBridge(AgentSurface(None), socket_path)
        bridge.start()
        config_path = runner.prepare_isolated_job_codex_home(arm_dir) / plugin_deployment.CONFIG_TOML_NAME
        config = {"agent_surface_server_enabled": injection._config_enabled(config_path)}
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        child_env = runner.build_sandbox_environment(gate_env, f"http://127.0.0.1:{server.server_port}/v1", isolated_agent=True, tap_client_token="provider-free-loopback-token")
        version_result = subprocess.run(
            runner.build_bwrap_argv(repo_root, arm_dir, [repo_root / injection._FIXTURE], ["codex", "--version"], gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path),
            env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30,
        )
        workload = _workload(ephemeral)
        workload_result = subprocess.run(
            runner.build_bwrap_argv(repo_root, arm_dir, [repo_root / injection._FIXTURE], workload, gate_env, agent_candidate_dir=candidate_dir, agent_surface_socket=socket_path),
            env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=45,
        )
        first = Receiver.requests[0] if Receiver.requests else None
        names = injection._bounded_names(first[1]) if first is not None else []
        exact, qualified = injection._inspect_name(names)
        return {
            "name": name, "argv": workload, "private_config": config,
            "mcp_preflight": injection._mcp_preflight(socket_path),
            "first_request": {"received": first is not None, "tool_descriptor_names": names, "exact_inspect_name": exact, "qualified_inspect_name": qualified},
            "receiver": {"loopback_only": bool(Receiver.requests) and all(path == "/v1/responses" and host == "127.0.0.1" for path, _body, host in Receiver.requests), "provider_escape": False, "request_count": len(Receiver.requests)},
            "process": {"codex_version": injection._version_from_output(version_result.stdout + "\n" + version_result.stderr), "version_exit_code": injection._safe_returncode(version_result.returncode), "workload_exit_code": injection._safe_returncode(workload_result.returncode)},
        }
    finally:
        if server is not None:
            server.shutdown(); server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        if bridge is not None:
            bridge.stop()
        shutil.rmtree(candidate_dir, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
        shutil.rmtree(arm_dir / "run" / ".codex-home", ignore_errors=True)


def _injects(arm: Mapping[str, Any]) -> bool:
    names = arm["first_request"]["tool_descriptor_names"]
    exact, qualified = injection._inspect_name(names)
    return exact or qualified


def _valid_arm(value: object, *, name: str, ephemeral: bool) -> bool:
    if not isinstance(value, dict) or set(value) != {"name", "argv", "private_config", "mcp_preflight", "first_request", "receiver", "process"}:
        return False
    if value["name"] != name or value["argv"] != _workload(ephemeral) or value["private_config"] != {"agent_surface_server_enabled": True}:
        return False
    preflight, first, receiver, process = value["mcp_preflight"], value["first_request"], value["receiver"], value["process"]
    if preflight != {"initialize_succeeded": True, "tools_list_succeeded": True, "tool_descriptor_names": [injection._TOOL]}:
        return False
    if not isinstance(first, dict) or set(first) != {"received", "tool_descriptor_names", "exact_inspect_name", "qualified_inspect_name"} or first["received"] is not True or not isinstance(first["tool_descriptor_names"], list):
        return False
    names = first["tool_descriptor_names"]
    if not 1 <= len(names) <= injection.MAX_TOOL_NAMES or any(type(item) is not str or len(item) > injection.MAX_TOOL_NAME_BYTES for item in names):
        return False
    exact, qualified = injection._inspect_name(names)
    if first["exact_inspect_name"] is not exact or first["qualified_inspect_name"] is not qualified:
        return False
    if receiver != {"loopback_only": True, "provider_escape": False, "request_count": 1}:
        return False
    return (
        isinstance(process, dict)
        and set(process) == {"codex_version", "version_exit_code", "workload_exit_code"}
        and process["codex_version"] == "0.147.0"
        and type(process["version_exit_code"]) is int
        and process["version_exit_code"] == 0
        and type(process["workload_exit_code"]) is int
    )


def _conclusion(arms: Mapping[str, Any]) -> dict[str, bool]:
    ephemeral_injects = _injects(arms["ephemeral"])
    persistent_injects = _injects(arms["persistent"])
    return {"ephemeral_injects": ephemeral_injects, "persistent_injects": persistent_injects, "ephemeral_blocks_injection": not ephemeral_injects and persistent_injects}


def validate_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _exp, evidence_path, manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > injection.MAX_EVIDENCE_BYTES or manifest_path.stat().st_size > injection.MAX_MANIFEST_BYTES:
            raise ProviderFreeError("provider-free artifact exceeds its byte limit")
        evidence, manifest = json.loads(evidence_path.read_bytes()), json.loads(manifest_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ProviderFreeError("provider-free evidence or artifact manifest is missing/invalid") from exc
    if not isinstance(evidence, dict) or set(evidence) != {"schema", "identity", "sandbox", "arms", "conclusion", "gate_passed"} or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("identity") != expected_identity(record):
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    if evidence.get("sandbox") != {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}:
        raise ProviderFreeError("provider-free evidence has an invalid sandbox contract")
    arms = evidence.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"ephemeral", "persistent"} or not _valid_arm(arms["ephemeral"], name="ephemeral", ephemeral=True) or not _valid_arm(arms["persistent"], name="persistent", ephemeral=False):
        raise ProviderFreeError("provider-free evidence has an invalid arm")
    conclusion = _conclusion(arms)
    if evidence.get("conclusion") != conclusion or evidence.get("gate_passed") is not conclusion["ephemeral_blocks_injection"]:
        raise ProviderFreeError("provider-free evidence conclusion differs from arms")
    expected_manifest = {"schema": MANIFEST_SCHEMA, "final_status": 0 if evidence["gate_passed"] else 1, "identity": expected_identity(record), "evidence": {"path": evidence_path.name}}
    if manifest != expected_manifest:
        raise ProviderFreeError("provider-free artifact manifest differs from evidence")
    return evidence_path, manifest_path


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    assert_current_authority(record, host_home)
    exp_dir, evidence_path, manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    gate_env = dict(environ); gate_env["VENUS_TOKEN"] = "provider-free-loopback-only"
    arms = {name: _run_arm(name, ephemeral, repo_root=repo_root, exp_dir=exp_dir, gate_env=gate_env) for name, ephemeral in _ARMS}
    conclusion = _conclusion(arms)
    evidence = {"schema": EVIDENCE_SCHEMA, "identity": identity, "sandbox": {"runner_bwrap": True, "gateway": "codex-tap-gpt56", "network": "loopback-only"}, "arms": arms, "conclusion": conclusion, "gate_passed": conclusion["ephemeral_blocks_injection"]}
    injection._atomic_write(evidence_path, evidence)
    injection._atomic_write(manifest_path, {"schema": MANIFEST_SCHEMA, "final_status": 0 if evidence["gate_passed"] else 1, "identity": identity, "evidence": {"path": evidence_path.name}})
    validate_artifacts(repo_root, record)
    return 0 if evidence["gate_passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--job", required=True); parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = protocol.load_state(args.state_root, args.job)
        return run_job(record, repo_root=Path(__file__).resolve().parents[2], host_home=Path.home(), environ=os.environ)
    except (ProviderFreeError, plugin_deployment.PluginAuthorityError, protocol.ProtocolError, runner.PilotError) as exc:
        print(f"provider-free-agent-surface-mcp-ephemeral-differential: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
