#!/usr/bin/env python3
"""Run one offline, installed-plugin discovery probe for a CVM job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment
from scripts.pilot.cvm_job import protocol


SCENARIO = "installed-plugin"
EVIDENCE_SCHEMA = "text-to-cad.provider-free-installed-plugin-evidence/1"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
SANDBOX_HOME = Path("/home/pilot")
SANDBOX_CODEX_HOME = SANDBOX_HOME / ".codex"
SANDBOX_PUBLISH_TREE = Path(plugin_deployment.SANDBOX_MARKETPLACE_SOURCE)
CODEX_ARGS = (
    "plugin",
    "list",
    "--marketplace",
    plugin_deployment.MARKETPLACE_NAME,
    "--json",
)
SYSTEM_PATHS = (
    Path("/usr"),
    Path("/etc/alternatives"),
    Path("/etc/group"),
    Path("/etc/ld.so.cache"),
    Path("/etc/ld.so.conf"),
    Path("/etc/ld.so.conf.d"),
    Path("/etc/localtime"),
    Path("/etc/nsswitch.conf"),
    Path("/etc/os-release"),
    Path("/etc/passwd"),
)
PASSTHROUGH_ENV = ("LANG", "LC_ALL", "TZ")
RUNNER_ENV = ("HOME", "PATH", *PASSTHROUGH_ENV)
AUTHORITY_FIELDS = (
    "deployment_id",
    "source_git_sha",
    "prepared_manifest_digest",
    "installed_manifest_digest",
    "codex_home_manifest_digest",
    "version",
)


class ProviderFreeError(RuntimeError):
    """The provider-free discovery contract was not satisfied."""


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return {name: str(getattr(receipt, name)) for name in AUTHORITY_FIELDS}


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    authority = record.get("plugin_authority")
    if not isinstance(authority, dict) or set(authority) != set(AUTHORITY_FIELDS):
        raise ProviderFreeError("job has invalid plugin-authority binding")
    if any(
        not isinstance(authority[name], str) or not authority[name]
        for name in AUTHORITY_FIELDS
    ):
        raise ProviderFreeError("job has invalid plugin-authority values")
    job = record.get("job")
    expected_exp_dir = f"outputs/{job}" if isinstance(job, str) else None
    if (
        record.get("provider_free") is not True
        or record.get("scenario") != SCENARIO
        or record.get("object") != SCENARIO
        or record.get("token_slot") is not None
        or record.get("exp_dir") != expected_exp_dir
    ):
        raise ProviderFreeError("job is not a provider-free installed-plugin request")
    return {
        "job": record.get("job"),
        "scenario": SCENARIO,
        "plugin_selector": plugin_deployment.PLUGIN_SELECTOR,
        "marketplace": plugin_deployment.MARKETPLACE_NAME,
        "authority": dict(authority),
    }


def assert_current_authority(
    record: Mapping[str, Any], host_home: Path
) -> plugin_deployment.DeploymentReceipt:
    try:
        receipt = plugin_deployment.resolve_current_authority(host_home)
    except plugin_deployment.PluginAuthorityError as exc:
        raise ProviderFreeError(f"plugin authority is unavailable: {exc}") from exc
    if authority_identity(receipt) != record.get("plugin_authority"):
        raise ProviderFreeError("current plugin authority differs from submitted job")
    return receipt


def build_child_env(environ: Mapping[str, str]) -> dict[str, str]:
    child = {name: environ[name] for name in PASSTHROUGH_ENV if environ.get(name)}
    child.update(
        {
            "CODEX_HOME": os.fspath(SANDBOX_CODEX_HOME),
            "HOME": os.fspath(SANDBOX_HOME),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "XDG_CACHE_HOME": "/tmp/cache",
        }
    )
    return child


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Keep provider credentials and proxy settings out of the runner too."""

    runner = {name: environ[name] for name in RUNNER_ENV if environ.get(name)}
    if "HOME" not in runner or "PATH" not in runner:
        raise ProviderFreeError("provider-free runner requires HOME and PATH")
    runner["PYTHONDONTWRITEBYTECODE"] = "1"
    return runner


def _trusted_usr_executable(value: str, label: str) -> Path:
    executable = Path(value).resolve()
    try:
        executable.relative_to("/usr")
    except ValueError as exc:
        raise ProviderFreeError(f"{label} must resolve under /usr") from exc
    return executable


def _trusted_bwrap(value: str) -> Path:
    executable = Path(value).resolve()
    if executable != Path("/usr/bin/bwrap"):
        raise ProviderFreeError("bwrap must resolve to /usr/bin/bwrap")
    return executable


def resolve_runtime(environ: Mapping[str, str]) -> tuple[Path, Path]:
    bwrap = shutil.which("bwrap", path=environ.get("PATH"))
    codex = shutil.which("codex", path=environ.get("PATH"))
    if not bwrap:
        raise ProviderFreeError("bwrap is not installed")
    if not codex:
        raise ProviderFreeError("codex is not installed")
    return (
        _trusted_bwrap(bwrap),
        _trusted_usr_executable(codex, "codex"),
    )


def build_bwrap_argv(
    *,
    bwrap: Path,
    codex: Path,
    job_codex_home: Path,
    job_publish_tree: Path,
    system_paths: Sequence[Path] | None = None,
) -> list[str]:
    argv = [
        os.fspath(bwrap),
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--cap-drop",
        "ALL",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        os.fspath(SANDBOX_HOME),
        "--dir",
        "/opt",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--bind",
        os.fspath(job_codex_home),
        os.fspath(SANDBOX_CODEX_HOME),
        "--ro-bind",
        os.fspath(job_publish_tree),
        os.fspath(SANDBOX_PUBLISH_TREE),
    ]
    for path in system_paths if system_paths is not None else SYSTEM_PATHS:
        if path.exists():
            argv.extend(("--ro-bind", os.fspath(path), os.fspath(path)))
    argv.extend(
        (
            "--die-with-parent",
            "--chdir",
            os.fspath(SANDBOX_HOME),
            "--",
            os.fspath(codex),
            *CODEX_ARGS,
        )
    )
    return argv


def _absolute_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item for nested in value.values() for item in _absolute_strings(nested)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _absolute_strings(nested)]
    return [value] if isinstance(value, str) and value.startswith("/") else []


def validate_codex_command(value: Any) -> list[str]:
    if not isinstance(value, list) or value[1:] != list(CODEX_ARGS):
        raise ProviderFreeError("provider-free evidence has an invalid Codex command")
    if not value or not isinstance(value[0], str):
        raise ProviderFreeError("provider-free evidence has an invalid Codex command")
    executable = Path(value[0])
    if not executable.is_absolute() or executable.parts[:2] != ("/", "usr"):
        raise ProviderFreeError("provider-free evidence has an unaudited Codex path")
    if ".." in executable.parts:
        raise ProviderFreeError("provider-free evidence has an unaudited Codex path")
    return value


def validate_bwrap_identity(value: Any) -> str:
    if value != "/usr/bin/bwrap":
        raise ProviderFreeError("provider-free evidence has an unaudited bwrap path")
    return value


def validate_plugin_list(payload: Any, expected_version: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"installed", "available"}:
        raise ProviderFreeError("Codex plugin list JSON has an invalid shape")
    installed = payload["installed"]
    if (
        not isinstance(installed, list)
        or len(installed) != 1
        or payload["available"] != []
    ):
        raise ProviderFreeError("Codex did not report exactly one installed plugin")
    plugin = installed[0]
    if not isinstance(plugin, dict):
        raise ProviderFreeError("installed plugin entry is malformed")
    expected = {
        "pluginId": plugin_deployment.PLUGIN_SELECTOR,
        "name": "cad",
        "marketplaceName": plugin_deployment.MARKETPLACE_NAME,
        "version": expected_version,
        "installed": True,
        "enabled": True,
    }
    if any(plugin.get(key) != value for key, value in expected.items()):
        raise ProviderFreeError(
            "installed plugin identity, version, or enabled state differs"
        )
    source = plugin.get("source")
    marketplace_source = plugin.get("marketplaceSource")
    if source != {"source": "local", "path": os.fspath(SANDBOX_PUBLISH_TREE)}:
        raise ProviderFreeError("plugin source is not the sandbox publish snapshot")
    if marketplace_source != {
        "sourceType": "local",
        "source": os.fspath(SANDBOX_PUBLISH_TREE),
    }:
        raise ProviderFreeError("marketplace source is not the sandbox publish snapshot")
    allowed_roots = (SANDBOX_PUBLISH_TREE, SANDBOX_CODEX_HOME)
    for raw in _absolute_strings(payload):
        path = Path(raw)
        if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
            raise ProviderFreeError("plugin list exposed a path outside sandbox snapshots")
    return plugin


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    exp_dir = repo_root / str(record["exp_dir"])
    return exp_dir, exp_dir / "provider-free-evidence.json", exp_dir / "artifact_manifest.json"


def validate_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    verify_evidence_digest: bool = True,
) -> tuple[Path, Path]:
    _, evidence_path, manifest_path = artifact_paths(repo_root, record)
    try:
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError(
            "provider-free evidence or artifact manifest is missing/invalid"
        ) from exc
    identity = expected_identity(record)
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema",
        "identity",
        "sandbox",
        "command",
        "environment_keys",
        "plugin_list",
    }:
        raise ProviderFreeError("provider-free evidence has an invalid shape")
    if (
        evidence.get("schema") != EVIDENCE_SCHEMA
        or evidence.get("identity") != identity
    ):
        raise ProviderFreeError("provider-free evidence identity differs from job")
    sandbox = evidence.get("sandbox")
    if (
        not isinstance(sandbox, dict)
        or set(sandbox) != {"network", "repo_mounted", "bwrap"}
        or sandbox.get("network") != "unshared"
        or sandbox.get("repo_mounted") is not False
    ):
        raise ProviderFreeError(
            "provider-free evidence has an invalid sandbox contract"
        )
    validate_bwrap_identity(sandbox.get("bwrap"))
    validate_codex_command(evidence.get("command"))
    environment_keys = evidence.get("environment_keys")
    required_keys = {"CODEX_HOME", "HOME", "PATH", "XDG_CACHE_HOME"}
    allowed_keys = required_keys.union(PASSTHROUGH_ENV)
    if (
        not isinstance(environment_keys, list)
        or any(not isinstance(item, str) for item in environment_keys)
        or environment_keys != sorted(set(environment_keys))
        or not required_keys.issubset(environment_keys)
        or not set(environment_keys).issubset(allowed_keys)
    ):
        raise ProviderFreeError(
            "provider-free evidence has an invalid environment contract"
        )
    validate_plugin_list(evidence.get("plugin_list"), identity["authority"]["version"])
    expected_manifest = {
        "schema": MANIFEST_SCHEMA,
        "final_status": 0,
        "identity": identity,
        "evidence": {"path": evidence_path.name},
    }
    if verify_evidence_digest:
        expected_manifest["evidence"]["sha256"] = hashlib.sha256(
            evidence_bytes
        ).hexdigest()
        valid_manifest = manifest == expected_manifest
    else:
        evidence_marker = manifest.get("evidence") if isinstance(manifest, dict) else None
        valid_manifest = (
            isinstance(evidence_marker, dict)
            and set(evidence_marker) == {"path", "sha256"}
            and evidence_marker.get("path") == evidence_path.name
            and isinstance(manifest, dict)
            and set(manifest) == set(expected_manifest)
            and manifest.get("schema") == expected_manifest["schema"]
            and manifest.get("final_status") == expected_manifest["final_status"]
            and manifest.get("identity") == identity
        )
    if not valid_manifest:
        raise ProviderFreeError("provider-free artifact manifest differs from evidence")
    return evidence_path, manifest_path


def run_job(
    record: Mapping[str, Any],
    *,
    repo_root: Path,
    host_home: Path,
    environ: Mapping[str, str],
    run: Any = subprocess.run,
) -> int:
    identity = expected_identity(record)
    receipt = assert_current_authority(record, host_home)
    exp_dir, evidence_path, manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    job_home = exp_dir / "run/codex-home"
    publish_tree = exp_dir / "run/publish-tree"
    plugin_deployment.materialize_job_codex_home(receipt, job_home)
    plugin_deployment.materialize_job_publish_tree(receipt, publish_tree)
    bwrap, codex = resolve_runtime(environ)
    argv = build_bwrap_argv(
        bwrap=bwrap,
        codex=codex,
        job_codex_home=job_home,
        job_publish_tree=publish_tree,
    )
    completed = run(
        argv,
        env=build_child_env(environ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProviderFreeError(f"offline Codex plugin list exited {completed.returncode}")
    try:
        plugin_list = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("Codex plugin list did not return JSON") from exc
    validate_plugin_list(plugin_list, receipt.version)
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "identity": identity,
        "sandbox": {
            "network": "unshared",
            "repo_mounted": False,
            "bwrap": os.fspath(bwrap),
        },
        "command": [os.fspath(codex), *CODEX_ARGS],
        "environment_keys": sorted(build_child_env(environ)),
        "plugin_list": plugin_list,
    }
    _atomic_write(evidence_path, evidence)
    evidence_bytes = evidence_path.read_bytes()
    _atomic_write(
        manifest_path,
        {
            "schema": MANIFEST_SCHEMA,
            "final_status": 0,
            "identity": identity,
            "evidence": {
                "path": evidence_path.name,
                "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            },
        },
    )
    validate_artifacts(repo_root, record)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = protocol.load_state(args.state_root, args.job)
        return run_job(
            record,
            repo_root=Path(__file__).resolve().parents[2],
            host_home=Path.home(),
            environ=os.environ,
        )
    except (ProviderFreeError, plugin_deployment.PluginAuthorityError, protocol.ProtocolError) as exc:
        print(f"provider-free-installed-plugin: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
