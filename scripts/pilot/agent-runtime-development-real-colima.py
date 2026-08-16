#!/usr/bin/env python3
"""Launch exactly one paid Development cup_cup_033 Agent job through Venus."""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot.agent_runtime.development_supervisor import (
    DockerEngine,
    execute as execute_agent,
    fixed_candidate_request,
)
from scripts.pilot.agent_runtime.development_venus_proxy import VENUS_BASE_URL


IMAGE = "sha256:a64ae96f4703bb8dfdbce1159106f606f1f00e1bf05991fa4bcabe27a0bfedc2"
PRICING_AUTHORITY = "iWiki-4020336897-v54-2026-08-14"
CLASSIFICATION = "Development/Not Sealed/Not Formal"
_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}\Z")


def read_venus_token(path: Path) -> str:
    """Read one literal VENUS_TOKEN without evaluating shell syntax."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("secret env reference must be one regular file")
    payload = path.read_bytes()
    if b"\r" in payload:
        raise ValueError("secret env reference cannot contain CR bytes")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise ValueError("secret env reference must be UTF-8") from error
    values: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != "VENUS_TOKEN":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not value or any(item in value for item in ("$", "`", "\n", "\r")):
            raise ValueError("VENUS_TOKEN must be one non-empty literal")
        values.append(value)
    if len(values) != 1:
        raise ValueError("secret env reference must contain exactly one VENUS_TOKEN")
    return values[0]


def read_ledger(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("proxy ledger is not valid JSONL") from error
    return rows


def _usd(value: Decimal) -> str:
    return f"{value:.6f}"


def public_accounting(rows: list[dict[str, object]]) -> dict[str, object]:
    """Summarize one job ledger without upgrading an upper bound to actual USD."""

    terminal = next((row for row in reversed(rows) if row.get("event") == "terminal"), None)
    if terminal is None:
        raise ValueError("proxy terminal ledger row is missing")
    reserves = [row for row in rows if row.get("event") == "reserve"]
    settles = [row for row in rows if row.get("event") == "settle"]
    usage = {"inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0}
    settled = Decimal(0)
    for row in settles:
        item = row.get("usage")
        if not isinstance(item, dict):
            raise ValueError("settlement usage is missing")
        for key in usage:
            value = item.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("settlement usage is invalid")
            usage[key] += value
        settled += Decimal(str(row["settledCostUpperBoundUsd"]))
    return {
        "attemptCount": terminal.get("attempts"),
        "mayHaveReachedAttemptCount": sum(row.get("mayHaveReachedModel") is True for row in reserves),
        "usage": usage,
        "settledCostUpperBoundUsd": _usd(settled),
        "unresolvedReservedUsd": terminal.get("unresolvedReservedUsd"),
        "actualUsd": None,
        "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent",
        "listenerAbsent": terminal.get("listenerAbsent"),
    }


class Docker:
    def __init__(self, executable: str, context: str) -> None:
        self.executable = executable
        self.context = context

    def run(self, *args: str, input_bytes: bytes | None = None, check: bool = True):
        return subprocess.run(
            [self.executable, "--context", self.context, *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def absent(self, kind: str, name: str) -> bool:
        return self.run(kind, "inspect", name, check=False).returncode != 0


def _seed_secret_volume(docker: Docker, volume: str, client: str, upstream: str) -> None:
    docker.run(
        "run", "--rm", "--pull", "never", "--read-only", "--user", "0:0",
        "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=volume,src={volume},dst=/secrets",
        "--entrypoint", "/usr/bin/chmod", IMAGE, "0777", "/secrets",
    )
    source = (
        "import json,pathlib,sys; v=json.load(sys.stdin); "
        "[(p.write_text(v[n],encoding='utf-8'),p.chmod(0o400)) "
        "for n in ('client-token','upstream-token') for p in (pathlib.Path('/secrets')/n,)]"
    )
    docker.run(
        "run", "--rm", "--interactive", "--pull", "never", "--read-only",
        "--user", "65532:65532", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--mount", f"type=volume,src={volume},dst=/secrets",
        "--entrypoint", "/usr/bin/python3.12", IMAGE, "-c", source,
        input_bytes=json.dumps({"client-token": client, "upstream-token": upstream}).encode(),
    )


def _seed_prior_total(docker: Docker, volume: str, payload: bytes) -> None:
    docker.run(
        "run", "--rm", "--pull", "never", "--read-only", "--user", "0:0",
        "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=volume,src={volume},dst=/evidence",
        "--entrypoint", "/usr/bin/chmod", IMAGE, "0777", "/evidence",
    )
    if payload:
        docker.run(
            "run", "--rm", "--interactive", "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,src={volume},dst=/evidence",
            "--entrypoint", "/usr/bin/python3.12", IMAGE, "-c",
            "import pathlib,sys; pathlib.Path('/evidence/total-ledger.jsonl').write_bytes(sys.stdin.buffer.read())",
            input_bytes=payload,
        )


def _probe_health(docker: Docker, internal: str, proxy: str) -> None:
    source = f'''
import http.client,time
for _ in range(100):
    try:
        c=http.client.HTTPConnection({proxy!r},8080,timeout=1); c.request("GET","/healthz")
        r=c.getresponse(); r.read(); c.close()
        if r.status==200: raise SystemExit(0)
    except OSError: pass
    time.sleep(.1)
raise SystemExit(1)
'''.encode()
    docker.run(
        "run", "--rm", "--interactive", "--pull", "never", "--read-only",
        "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--entrypoint", "/usr/bin/python3.12",
        IMAGE, "-", input_bytes=source,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid-development", action="store_true", required=True)
    parser.add_argument("--secret-env-file", type=Path, required=True)
    parser.add_argument("--prior-total-ledger", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--host-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--docker-context", default="colima-sealed-agent-runtime")
    args = parser.parse_args(argv)
    if not _NAME.fullmatch(args.suffix):
        raise SystemExit("suffix is invalid")
    if args.host_stage.exists() or args.output.exists():
        raise SystemExit("host stage and output must both be fresh")
    if args.prior_total_ledger.is_symlink() or not args.prior_total_ledger.is_file():
        raise SystemExit("prior total ledger must be one regular file")
    prior_total = args.prior_total_ledger.read_bytes()
    prior_total_digest = "sha256:" + hashlib.sha256(prior_total).hexdigest()
    if prior_total and not prior_total.endswith(b"\n"):
        raise SystemExit("prior total ledger must end with LF")
    if prior_total:
        read_ledger(args.prior_total_ledger)
    upstream_token = read_venus_token(args.secret_env_file)
    client_token = secrets.token_urlsafe(32)
    clean = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, check=True,
    ).stdout
    if clean:
        raise SystemExit("Git worktree must be clean before paid Development dispatch")
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode("ascii").strip()
    args.host_stage.mkdir(mode=0o755, parents=True)
    args.output.mkdir(mode=0o700, parents=True)
    docker = Docker(args.docker, args.docker_context)
    internal = f"t2c-{args.suffix}-internal"
    egress = f"t2c-{args.suffix}-egress"
    proxy = f"t2c-{args.suffix}-proxy"
    evidence = f"t2c-{args.suffix}-evidence"
    secret_volume = f"t2c-{args.suffix}-secrets"
    stage = "runtime-identity"
    proxy_id: str | None = None
    agent_terminal: dict[str, object] | None = None
    failure: dict[str, object] | None = None
    proxy_stopped = False
    try:
        observed = json.loads(docker.run("image", "inspect", IMAGE).stdout)[0]
        if (observed.get("Id"), observed.get("Architecture"), observed.get("Os")) != (IMAGE, "amd64", "linux"):
            raise RuntimeError("fixed image identity or platform drifted")
        for kind, name in (("container", proxy), ("network", internal), ("network", egress), ("volume", evidence), ("volume", secret_volume)):
            if not docker.absent(kind, name):
                raise RuntimeError("exact launcher resource already exists")
        stage = "stage-immutable-inputs"
        staged_pilot = args.host_stage / "scripts/pilot"
        (staged_pilot / "agent_runtime").mkdir(mode=0o755, parents=True)
        for relative in (
            "scripts/pilot/agent-runtime-development-proxy.py",
            "scripts/pilot/agent_runtime/development_venus_proxy.py",
        ):
            target = args.host_stage / relative
            shutil.copyfile(REPO_ROOT / relative, target)
            target.chmod(0o444)
        staged_source = args.host_stage / "models/agent-runtime/cup_cup_033/source"
        staged_input = args.host_stage / "models/agent-runtime/cup_cup_033/input"
        staged_source.mkdir(mode=0o755, parents=True)
        staged_input.mkdir(mode=0o755, parents=True)
        shutil.copyfile(REPO_ROOT / "models/agent-runtime/cup_cup_033/source/cup_cup_033.implicit.js", staged_source / "cup_cup_033.implicit.js")
        shutil.copyfile(REPO_ROOT / "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply", staged_input / "cup_cup_033.ply")
        staged_workload = args.host_stage / "workload.json"
        shutil.copyfile(args.workload, staged_workload)
        staged_workload.chmod(0o444)
        stage = "network-volume-create"
        docker.run("network", "create", "--internal", internal)
        docker.run("network", "create", egress)
        docker.run("volume", "create", evidence)
        docker.run("volume", "create", secret_volume)
        stage = "secret-and-ledger-seed"
        _seed_secret_volume(docker, secret_volume, client_token, upstream_token)
        _seed_prior_total(docker, evidence, prior_total)
        stage = "proxy-create-start"
        proxy_id = docker.run(
            "create", "--name", proxy, "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,src={args.host_stage},dst=/opt/text-to-cad,readonly",
            "--mount", f"type=volume,src={secret_volume},dst=/run/secrets,readonly",
            "--mount", f"type=volume,src={evidence},dst=/evidence",
            "--entrypoint", "/usr/bin/python3.12", IMAGE,
            "/opt/text-to-cad/scripts/pilot/agent-runtime-development-proxy.py",
            "--job-id", f"cup-cup-033-{args.suffix}",
            "--ledger", "/evidence/job-ledger.jsonl",
            "--total-ledger", "/evidence/total-ledger.jsonl",
            "--client-token-file", "/run/secrets/client-token",
            "--upstream-token-file", "/run/secrets/upstream-token",
            "--max-attempts", "16", "--max-request-bytes", "200000",
            "--max-output-tokens", "40000", "--upstream-timeout", "180",
        ).stdout.decode("ascii").strip()
        docker.run("network", "connect", egress, proxy)
        docker.run("start", proxy)
        _probe_health(docker, internal, proxy)
        stage = "agent-supervisor"
        agent_output = args.host_stage / "agent-supervisor"
        agent_output.mkdir(mode=0o700)
        request = fixed_candidate_request(
            repo_root=REPO_ROOT, image_id=IMAGE,
            source_dir=staged_source, input_dir=staged_input,
            output_dir=agent_output, workload_path=staged_workload,
            internal_network=internal,
            proxy_base_url=f"http://{proxy}:8080/v1",
            proxy_client_token=client_token,
        )
        try:
            agent_terminal = execute_agent(
                request,
                engine=DockerEngine(args.docker, context=args.docker_context),
            )
        finally:
            if (agent_output / "supervisor/terminal.json").is_file():
                agent_terminal = json.loads((agent_output / "supervisor/terminal.json").read_bytes())
            shutil.copytree(agent_output, args.output / "agent-supervisor")
    except BaseException as error:
        failure = {"stage": stage, "category": type(error).__name__}
    finally:
        if not docker.absent("container", proxy):
            docker.run("stop", "--time", "10", proxy, check=False)
            proxy_stopped = True
            evidence_output = args.output / "proxy-evidence"
            evidence_output.mkdir(exist_ok=True)
            docker.run("cp", f"{proxy}:/evidence/.", str(evidence_output), check=False)
            logs = docker.run("logs", proxy, check=False)
            (args.output / "proxy.stdout").write_bytes(logs.stdout)
            (args.output / "proxy.stderr").write_bytes(logs.stderr)
        docker.run("rm", "--force", proxy, check=False)
        docker.run("volume", "rm", "--force", secret_volume, check=False)
        docker.run("volume", "rm", "--force", evidence, check=False)
        docker.run("network", "rm", internal, check=False)
        docker.run("network", "rm", egress, check=False)
        cleanup = {
            "proxyContainerAbsent": docker.absent("container", proxy),
            "internalNetworkAbsent": docker.absent("network", internal),
            "egressNetworkAbsent": docker.absent("network", egress),
            "evidenceVolumeAbsent": docker.absent("volume", evidence),
            "secretVolumeAbsent": docker.absent("volume", secret_volume),
        }
        accounting: dict[str, object] | None = None
        job_ledger = args.output / "proxy-evidence/job-ledger.jsonl"
        if job_ledger.is_file():
            try:
                accounting = public_accounting(read_ledger(job_ledger))
            except ValueError:
                accounting = None
        receipt = {
            "schema": "text-to-cad.cup-cup-033-development-real-colima/1",
            "classification": CLASSIFICATION,
            "status": "development-completed" if failure is None and agent_terminal and agent_terminal.get("status") == "development-succeeded" and accounting is not None and all(cleanup.values()) else "development-failed",
            "fixtureId": "cup_cup_033",
            "gitSha": git_sha,
            "imageId": IMAGE,
            "dockerContext": args.docker_context,
            "runtimeProfile": "sealed-agent-runtime",
            "platform": {"architecture": "amd64", "os": "linux"},
            "venusBaseUrl": VENUS_BASE_URL,
            "model": "gpt-5.6-sol",
            "pricingAuthority": PRICING_AUTHORITY,
            "policy": {
                "maxJobs": 1, "maxAttempts": 16, "maxRequestBytes": 200000,
                "maxOutputTokens": 40000, "maxJobSeconds": 2700,
                "worstCaseAttemptUsd": "2.450000", "worstCaseJobUsd": "39.200000",
            },
            "priorTotalLedgerDigest": prior_total_digest,
            "jobId": agent_terminal.get("jobId") if agent_terminal else None,
            "proxyContainerId": proxy_id,
            "proxyStopped": proxy_stopped,
            "agentTerminal": agent_terminal,
            "accounting": accounting,
            "cleanup": cleanup,
            "failure": failure,
            "secretPersistence": "none; provider and client capabilities removed with exact secret volume",
        }
        (args.output / "receipt.json").write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return 0 if receipt["status"] == "development-completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
