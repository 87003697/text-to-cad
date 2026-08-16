#!/usr/bin/env python3
"""Run one CVM MVP Venus pilot for cup_cup_033 (Development only)."""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, NamedTuple, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot.agent_runtime.development_venus_proxy import (  # noqa: E402
    CostPolicy,
    DevelopmentProxy,
    VENUS_BASE_URL,
)


CLASSIFICATION = "CVM MVP Venus Pilot — cup_cup_033"
FIXED_INPUT = Path("models/toys4k/cup_cup_033.ply")
FIXED_INPUT_SHA256 = "3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67"
MODEL = "gpt-5.6-sol"
CODEX_VERSION = "0.142.1"
MAX_SECONDS = 45 * 60
OUTER_BUILD_SECONDS = 10 * 60
PRICING_AUTHORITY = "iWiki-4020336897-v54-2026-08-14"
COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")


class MvpError(RuntimeError):
    pass


class RunPlan(NamedTuple):
    repo_root: Path
    exp_dir: Path
    input_path: Path
    initial_source: Path
    input_sha256: str
    source_sha256: str
    source_revision: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_nonempty(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise MvpError(f"{label} must be one nonempty regular file")


def prepare_plan(
    repo_root: Path, group: str, exp: str, initial_source: Path,
    source_revision: str,
) -> RunPlan:
    if not COMPONENT.fullmatch(group) or not COMPONENT.fullmatch(exp):
        raise MvpError("group and exp must be safe path components")
    if not SOURCE_REVISION.fullmatch(source_revision):
        raise MvpError("source revision must be one exact 40-character lowercase Git SHA")
    input_path = repo_root / FIXED_INPUT
    _regular_nonempty(input_path, "fixed cup_cup_033 input")
    observed = sha256(input_path)
    if observed != FIXED_INPUT_SHA256:
        raise MvpError("fixed cup_cup_033 input digest mismatch")
    initial_source = initial_source.resolve(strict=True)
    _regular_nonempty(initial_source, "initial source")
    exp_dir = repo_root / "outputs" / group / exp
    if exp_dir.exists():
        raise MvpError("output experiment must be fresh")
    return RunPlan(
        repo_root, exp_dir, input_path, initial_source, observed,
        sha256(initial_source), source_revision,
    )


def codex_config(proxy_url: str, client_token: str) -> str:
    return (
        f'model = "{MODEL}"\n'
        'model_provider = "venus"\n'
        "[features]\n"
        "code_mode = false\n"
        "[model_providers.venus]\n"
        'name = "Venus GPT-5.6 Sol Development Proxy"\n'
        f"base_url = {json.dumps(proxy_url)}\n"
        'wire_api = "responses"\n'
        f"experimental_bearer_token = {json.dumps(client_token)}\n"
    )


def build_prompt(working_source: Path, measurement_path: Path, glb_path: Path) -> str:
    return f"""You are running exactly one Development/MVP CAD review job.
This is not Sealed, not Formal, not Verified, and not a production completion.

The outer deterministic build has already produced the fixed source copy at:
{working_source}
It produced numeric measurement evidence at:
{measurement_path}
It also produced this GLB, which you must not open or parse:
{glb_path}

Your role is GPT-5.6 Sol review only. Read only the small source text and the
small measurement JSON. Review their geometry, numeric evidence, provenance,
and limitations; write only review.md, explicitly labeling the result as
"outer deterministic build + GPT-5.6 Sol review" and Development/MVP. Then stop.

Keep context bounded. Do not run git. Do not run find. Do not use head.
Do not use snapshot. Do not use a browser. Do not use matplotlib.
Do not run canonical-build. Do not read binary files as text or dump their
bytes. Do not open the PLY or GLB. Do not rebuild or export anything.
Do not modify the source. Do not run long commands or tests.
Do not recursively inspect the repo.
Do not access credentials or network configuration. Do not call this work Sealed,
Formal, Verified, or production complete. End with a concise result summary.
"""


def _terminate_process_group(process: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def run_process_group(
    command: Sequence[str], *, cwd: Path, prompt: bytes, stdout_path: Path,
    stderr_path: Path, environment: dict[str, str], timeout: int = MAX_SECONDS,
) -> tuple[int | None, bool, bool]:
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(command), cwd=cwd, stdin=subprocess.PIPE, stdout=stdout,
            stderr=stderr, env=environment, start_new_session=True,
        )
        timed_out = False
        try:
            process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        group_absent = _terminate_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            group_absent = False
        return process.returncode, timed_out, group_absent


def publish_stderr_evidence(
    source: Path, target: Path, *, forbidden_values: Sequence[str] = (),
) -> dict[str, object]:
    """Publish the closed stderr stream without placing its contents in JSON."""

    if source.is_symlink() or not source.is_file():
        raise MvpError("Codex stderr source must be one regular file")
    if target.exists() or target.is_symlink():
        raise MvpError("Codex stderr evidence target must be fresh")
    payload = source.read_bytes()
    for value in forbidden_values:
        if value and value.encode("utf-8") in payload:
            raise MvpError("Codex stderr contains a forbidden capability")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o600)
        if target.read_bytes() != payload:
            raise MvpError("Codex stderr evidence byte copy mismatch")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {
        "path": "run/codex-stderr.txt",
        "sha256": sha256(target),
        "bytes": len(payload),
        "transferPolicy": "included-canonical-byte-copy; run/stderr.log default-excluded",
    }


def outer_export_command(repo_root: Path, working_source: Path, glb_path: Path) -> list[str]:
    return [
        "node", os.fspath(repo_root / "packages/implicitjs/scripts/export.mjs"),
        "--input", os.fspath(working_source),
        "--output", os.fspath(glb_path),
        "--format", "glb", "--resolution", "64", "--max-cells", "1000000",
        "--json",
    ]


def _mesh_facts(path: Path, trimesh_module: Any) -> dict[str, object]:
    mesh = trimesh_module.load(path, force="mesh", process=False)
    if not hasattr(mesh, "vertices") and hasattr(mesh, "geometry"):
        mesh = trimesh_module.util.concatenate(tuple(mesh.geometry.values()))
    bounds_value = mesh.bounds.tolist() if hasattr(mesh.bounds, "tolist") else mesh.bounds
    bounds = [[float(value) for value in row] for row in bounds_value]
    return {
        "sha256": sha256(path), "bytes": path.stat().st_size,
        "vertices": len(mesh.vertices), "faces": len(mesh.faces),
        "bounds": bounds, "watertight": bool(mesh.is_watertight),
    }


def numeric_measurement(
    input_path: Path, glb_path: Path, export_command: Sequence[str], *,
    node_version: str, trimesh_module: Any | None = None,
) -> dict[str, object]:
    if trimesh_module is None:
        import trimesh as trimesh_module  # type: ignore[no-redef]
    return {
        "schema": "text-to-cad.cup-cup-033-numeric-measurement/1",
        "ownership": "outer-deterministic-provider-free",
        "input": _mesh_facts(input_path, trimesh_module),
        "glb": _mesh_facts(glb_path, trimesh_module),
        "tools": {
            "exportCommand": list(export_command),
            "exportParameters": {"format": "glb", "resolution": 64, "maxCells": 1_000_000},
            "measurementCommand": "trimesh.load(path, force='mesh', process=False)",
            "versions": {
                "node": node_version,
                "python": sys.version.split()[0],
                "trimesh": str(trimesh_module.__version__),
            },
        },
    }


def outer_deterministic_build(plan: RunPlan, working_source: Path, run_dir: Path) -> dict[str, object]:
    artifact = plan.exp_dir / "artifacts/cup_cup_033.glb"
    artifact.parent.mkdir()
    measurement_path = plan.exp_dir / "measurement/numeric-measurement.json"
    measurement_path.parent.mkdir()
    command = outer_export_command(plan.repo_root, working_source, artifact)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    status, timed_out, group_absent = run_process_group(
        command, cwd=plan.repo_root, prompt=b"",
        stdout_path=run_dir / "outer-build.stdout.log",
        stderr_path=run_dir / "outer-build.stderr.log",
        environment=environment, timeout=OUTER_BUILD_SECONDS,
    )
    if timed_out or status != 0 or not group_absent:
        raise MvpError("outer deterministic GLB export failed")
    _regular_nonempty(artifact, "outer deterministic GLB")
    try:
        node_version = subprocess.run(
            ["node", "--version"], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=environment, timeout=10,
        ).stdout.decode("ascii").strip()
        measurement = numeric_measurement(
            plan.input_path, artifact, command, node_version=node_version,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as error:
        raise MvpError("outer deterministic numeric measurement failed") from error
    _atomic_json(measurement_path, measurement)
    return {
        "ownership": "outer-deterministic-provider-free",
        "exportCommand": command,
        "parameters": {"format": "glb", "resolution": 64, "maxCells": 1_000_000},
        "timeoutSeconds": OUTER_BUILD_SECONDS,
        "exitCode": status, "timedOut": timed_out,
        "processGroupAbsent": group_absent,
        "glbSha256": sha256(artifact),
        "measurementSha256": sha256(measurement_path),
    }


def validate_outputs(exp_dir: Path) -> None:
    required = (
        "source/cup_cup_033.implicit.js",
        "artifacts/cup_cup_033.glb",
        "measurement/numeric-measurement.json",
        "review.md",
        "run/codex-events.jsonl",
        "run/stdout.log",
        "run/last-message.txt",
    )
    for relative in required:
        _regular_nonempty(exp_dir / relative, relative)
    stderr = exp_dir / "run/codex-stderr.txt"
    if stderr.is_symlink() or not stderr.is_file():
        raise MvpError("run/codex-stderr.txt must be a regular file")
    try:
        measurement = json.loads((exp_dir / "measurement/numeric-measurement.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MvpError("numeric measurement must be valid JSON") from error
    if not isinstance(measurement, dict) or not measurement:
        raise MvpError("numeric measurement must be a nonempty object")


def read_ledger(path: Path) -> list[dict[str, object]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MvpError("proxy ledger is not valid JSONL") from error
    if not all(isinstance(row, dict) for row in rows):
        raise MvpError("proxy ledger is not valid JSONL")
    return rows


def _usd(value: Decimal) -> str:
    return f"{value:.6f}"


def public_accounting(rows: list[dict[str, object]]) -> dict[str, object]:
    terminal = next((row for row in reversed(rows) if row.get("event") == "terminal"), None)
    if terminal is None:
        raise MvpError("proxy terminal ledger row is missing")
    reserves = [row for row in rows if row.get("event") == "reserve"]
    usage = {"inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0}
    settled = Decimal(0)
    for row in rows:
        if row.get("event") != "settle":
            continue
        item = row.get("usage")
        if not isinstance(item, dict):
            raise MvpError("settlement usage is invalid")
        for key in usage:
            value = item.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise MvpError("settlement usage is invalid")
            usage[key] += value
        settled += Decimal(str(row["settledCostUpperBoundUsd"]))
    return {
        "jobCount": 1,
        "attemptCount": terminal.get("attempts"),
        "mayHaveReachedAttemptCount": sum(row.get("mayHaveReachedModel") is True for row in reserves),
        "usage": usage,
        "settledCostUpperBoundUsd": _usd(settled),
        "unresolvedReservedUsd": terminal.get("unresolvedReservedUsd"),
        "actualUsd": None,
        "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent",
        "listenerAbsent": terminal.get("listenerAbsent"),
    }


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_artifact_manifest(exp_dir: Path, final_status: int, source_revision: str) -> None:
    if type(final_status) is not int:
        raise MvpError("final_status must be an integer")
    if not SOURCE_REVISION.fullmatch(source_revision):
        raise MvpError("source revision must be one exact 40-character lowercase Git SHA")
    files = []
    for path in sorted(exp_dir.rglob("*")):
        if path.is_symlink():
            raise MvpError("output contains a symlink")
        if path.is_file() and path.name not in {"artifact_manifest.json", ".artifact_manifest.json.tmp"}:
            files.append({
                "path": path.relative_to(exp_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    _atomic_json(exp_dir / "artifact_manifest.json", {
        "schema_version": 1, "workload_status": final_status,
        "final_status": final_status, "source_revision": source_revision,
        "build_ownership": "outer-deterministic-provider-free",
        "build_command": outer_export_command(
            REPO_ROOT, exp_dir / "source/cup_cup_033.implicit.js",
            exp_dir / "artifacts/cup_cup_033.glb",
        ),
        "build_parameters": {
            "format": "glb", "resolution": 64, "max_cells": 1_000_000,
        },
        "gpt_role": "review-only",
        "files": files,
    })


def _codex_command(codex: str, exp_dir: Path, last_message: Path) -> list[str]:
    return [
        codex, "-c", 'approval_policy="never"', "-m", MODEL, "exec",
        "--strict-config", "--ephemeral", "--skip-git-repo-check",
        "--sandbox", "workspace-write", "--json",
        "--output-last-message", os.fspath(last_message),
        "--cd", os.fspath(exp_dir), "-",
    ]


def seed_working_source(plan: RunPlan) -> Path:
    working_source = plan.exp_dir / "source/cup_cup_033.implicit.js"
    working_source.parent.mkdir()
    shutil.copyfile(plan.initial_source, working_source)
    if sha256(working_source) != plan.source_sha256:
        raise MvpError("fixed working source copy digest mismatch")
    return working_source


def execute(
    plan: RunPlan, *, codex: str = "codex",
    prior_total_ledger: Path | None = None,
    outer_builder: Callable[[RunPlan, Path, Path], dict[str, object]] = outer_deterministic_build,
    provider_factory: Callable[..., Any] = DevelopmentProxy,
) -> int:
    plan.exp_dir.mkdir(parents=True, mode=0o700)
    run_dir = plan.exp_dir / "run"
    run_dir.mkdir()
    working_source = seed_working_source(plan)
    job_ledger = run_dir / "job-ledger.jsonl"
    total_ledger = run_dir / "total-ledger.jsonl"
    if prior_total_ledger is not None:
        if prior_total_ledger.is_symlink() or not prior_total_ledger.is_file():
            raise MvpError("prior total ledger must be one regular file")
        payload = prior_total_ledger.read_bytes()
        if payload and not payload.endswith(b"\n"):
            raise MvpError("prior total ledger must end with LF")
        total_ledger.write_bytes(payload)
    else:
        total_ledger.touch()

    try:
        outer_build = outer_builder(plan, working_source, run_dir)
    except BaseException as error:
        job_ledger.touch(exist_ok=True)
        receipt = {
            "schema": "text-to-cad.cvm-cup-cup-033-development-mvp/1",
            "classification": CLASSIFICATION,
            "status": "development-mvp-failed",
            "notSealed": True, "notFormal": True, "notVerified": True,
            "fixtureId": "cup_cup_033", "sourceRevision": plan.source_revision,
            "launcherSha256": sha256(Path(__file__)),
            "runtime": {"environment": "CVM", "docker": False, "codexVersion": None},
            "model": MODEL, "wireApi": "responses", "codeMode": False,
            "pricingAuthority": PRICING_AUTHORITY,
            "buildOwnership": "outer-deterministic-provider-free",
            "gptRole": "review-only-not-run",
            "outerBuild": {
                "status": "failed",
                "ownership": "outer-deterministic-provider-free",
                "exportCommand": outer_export_command(
                    plan.repo_root, working_source,
                    plan.exp_dir / "artifacts/cup_cup_033.glb",
                ),
                "parameters": {"format": "glb", "resolution": 64, "maxCells": 1_000_000},
                "timeoutSeconds": OUTER_BUILD_SECONDS,
            },
            "stderrEvidence": {
                "path": "run/codex-stderr.txt", "sha256": None, "bytes": None,
                "transferPolicy": "not-run; paid dispatch zero before Codex",
            },
            "paidDispatchCount": 0,
            "accounting": {
                "jobCount": 0, "attemptCount": 0,
                "mayHaveReachedAttemptCount": 0,
                "usage": {"inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0},
                "settledCostUpperBoundUsd": "0.000000",
                "unresolvedReservedUsd": "0.000000", "actualUsd": None,
                "actualUsdUnavailableReason": "paid_dispatch_zero_outer_build_failed",
            },
            "policy": {"maxJobs": 1, "maxAttempts": 16, "maxRequestBytes": 200000,
                       "maxOutputTokens": 40000, "maxJobSeconds": MAX_SECONDS,
                       "worstCaseAttemptUsd": "2.450000", "worstCaseJobUsd": "39.200000",
                       "automaticWholeJobRetry": False},
            "failure": {"stage": "outer-deterministic-build", "category": type(error).__name__},
        }
        _atomic_json(plan.exp_dir / "receipt.json", receipt)
        write_artifact_manifest(plan.exp_dir, 1, plan.source_revision)
        return 1

    measurement_path = plan.exp_dir / "measurement/numeric-measurement.json"
    glb_path = plan.exp_dir / "artifacts/cup_cup_033.glb"
    prompt = build_prompt(working_source, measurement_path, glb_path).encode()
    (run_dir / "prompt.txt").write_bytes(prompt)

    upstream_token = os.environ.get("VENUS_TOKEN")
    if not upstream_token or "\n" in upstream_token or "\r" in upstream_token:
        raise MvpError("VENUS_TOKEN must be one nonempty environment value")
    observed_version = subprocess.run(
        [codex, "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.decode("utf-8", "replace").strip()
    if CODEX_VERSION not in observed_version:
        raise MvpError(f"Codex {CODEX_VERSION} is required")

    client_token = secrets.token_urlsafe(32)
    process_status: int | None = None
    timed_out = False
    process_group_absent = False
    failure: dict[str, str] | None = None
    private_absent = False
    policy = CostPolicy(max_attempts=16, max_request_bytes=200_000, max_output_tokens=40_000)
    with tempfile.TemporaryDirectory(prefix="t2c-cvm-cup-cup-033-") as private_name:
        private = Path(private_name)
        codex_home = private / ".codex"
        codex_home.mkdir(mode=0o700)
        config = codex_home / "config.toml"
        try:
            with provider_factory(
                VENUS_BASE_URL, job_ledger, upstream_token=upstream_token,
                client_token=client_token, job_id=f"cup_cup_033-{plan.exp_dir.name}",
                policy=policy, total_ledger_path=total_ledger,
            ) as proxy:
                config.write_text(codex_config(proxy.url, client_token), encoding="utf-8")
                config.chmod(0o600)
                environment = os.environ.copy()
                environment.pop("VENUS_TOKEN", None)
                environment.update({"HOME": os.fspath(private), "CODEX_HOME": os.fspath(codex_home)})
                process_status, timed_out, process_group_absent = run_process_group(
                    _codex_command(codex, plan.exp_dir, run_dir / "last-message.txt"),
                    cwd=plan.exp_dir, prompt=prompt,
                    stdout_path=run_dir / "codex-events.jsonl", stderr_path=run_dir / "stderr.log",
                    environment=environment,
                )
        except BaseException as error:
            failure = {"stage": "codex-or-proxy", "category": type(error).__name__}
        finally:
            config.unlink(missing_ok=True)
    private_absent = not Path(private_name).exists()

    try:
        stderr_evidence = publish_stderr_evidence(
            run_dir / "stderr.log", run_dir / "codex-stderr.txt",
            forbidden_values=(upstream_token, client_token),
        )
    except MvpError as error:
        failure = failure or {"stage": "stderr-evidence", "category": type(error).__name__}
        stderr_evidence = {
            "path": "run/codex-stderr.txt", "sha256": None, "bytes": None,
            "transferPolicy": "blocked; source absent, unsafe, or contained a capability",
        }

    events = run_dir / "codex-events.jsonl"
    if events.is_file():
        shutil.copyfile(events, run_dir / "stdout.log")
    accounting: dict[str, object] | None = None
    if job_ledger.is_file():
        try:
            accounting = public_accounting(read_ledger(job_ledger))
        except MvpError:
            failure = failure or {"stage": "accounting", "category": "MvpError"}
    validation_error: str | None = None
    try:
        validate_outputs(plan.exp_dir)
    except MvpError as error:
        validation_error = str(error)
    success = (
        failure is None and not timed_out and process_status == 0 and validation_error is None
        and accounting is not None and accounting.get("listenerAbsent") is True
        and process_group_absent and private_absent
    )
    receipt = {
        "schema": "text-to-cad.cvm-cup-cup-033-development-mvp/1",
        "classification": CLASSIFICATION,
        "status": "development-mvp-completed" if success else "development-mvp-failed",
        "notSealed": True, "notFormal": True, "notVerified": True,
        "fixtureId": "cup_cup_033", "sourceRevision": plan.source_revision,
        "launcherSha256": sha256(Path(__file__)),
        "runtime": {"environment": "CVM", "docker": False, "codexVersion": observed_version},
        "model": MODEL, "wireApi": "responses", "codeMode": False,
        "venusBaseUrl": VENUS_BASE_URL, "pricingAuthority": PRICING_AUTHORITY,
        "input": {"path": str(FIXED_INPUT), "sha256": plan.input_sha256},
        "initialSource": {"path": os.fspath(plan.initial_source), "sha256": plan.source_sha256,
                          "identityPolicy": "explicit MVP input; digest may differ from prior candidates"},
        "buildOwnership": "outer-deterministic-provider-free",
        "gptRole": "review-only",
        "outerBuild": outer_build,
        "stderrEvidence": stderr_evidence,
        "policy": {"maxJobs": 1, "maxAttempts": 16, "maxRequestBytes": 200000,
                   "maxOutputTokens": 40000, "maxJobSeconds": MAX_SECONDS,
                   "worstCaseAttemptUsd": "2.450000", "worstCaseJobUsd": "39.200000",
                   "automaticWholeJobRetry": False},
        "accounting": accounting,
        "paidDispatchCount": (
            accounting.get("mayHaveReachedAttemptCount") if accounting else None
        ),
        "process": {"exitCode": process_status, "timedOut": timed_out,
                    "processGroupAbsent": process_group_absent},
        "cleanup": {"proxyListenerAbsent": accounting.get("listenerAbsent") if accounting else None,
                    "privateControlAbsent": private_absent},
        "failure": failure, "validationError": validation_error,
        "actualUsd": None,
        "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent",
        "secretPersistence": "none; VENUS_TOKEN remained proxy-process memory and was removed from Codex environment",
    }
    _atomic_json(plan.exp_dir / "receipt.json", receipt)
    write_artifact_manifest(plan.exp_dir, 0 if success else 1, plan.source_revision)
    return 0 if success else 1


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True)
    parser.add_argument("--exp", required=True)
    parser.add_argument("--initial-source", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--prior-total-ledger", type=Path)
    parser.add_argument("--codex", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        plan = prepare_plan(
            REPO_ROOT, args.group, args.exp, args.initial_source,
            args.source_revision,
        )
        return execute(plan, codex=args.codex, prior_total_ledger=args.prior_total_ledger)
    except MvpError as error:
        print(f"CVM MVP preflight failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
