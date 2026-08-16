#!/usr/bin/env python3
"""Run the bounded provider-free SAI-010 Development conformance in Colima."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pilot.agent_runtime.development_supervisor import (
    DockerEngine,
    execute as execute_agent,
    fixed_candidate_request,
)


IMAGE = "sha256:a64ae96f4703bb8dfdbce1159106f606f1f00e1bf05991fa4bcabe27a0bfedc2"
DOCKER_EXECUTABLE = "docker"
DOCKER_CONTEXT = "colima-sealed-agent-runtime"


def run(*args: str, input_bytes: bytes | None = None, check: bool = True):
    return subprocess.run(
        [DOCKER_EXECUTABLE, "--context", DOCKER_CONTEXT, *args],
        input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=check,
    )


def absent(kind: str, name: str) -> bool:
    return run(kind, "inspect", name, check=False).returncode != 0


def main(argv=None) -> int:
    global DOCKER_EXECUTABLE, DOCKER_CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--docker-context", default="colima-sealed-agent-runtime")
    parser.add_argument("--host-stage", type=Path, required=True, help="fresh /Users path visible inside Colima")
    parser.add_argument("--output", type=Path, required=True, help="fresh retained host evidence directory")
    parser.add_argument("--suffix", default="sai010-dev-20260817")
    args = parser.parse_args(argv)
    DOCKER_EXECUTABLE = args.docker
    DOCKER_CONTEXT = args.docker_context
    if args.host_stage.exists() or args.output.exists():
        raise SystemExit("stage and output must both be absent")
    args.host_stage.mkdir(mode=0o700, parents=True)
    args.host_stage.chmod(0o755)
    args.output.mkdir(mode=0o700, parents=True)
    source_root = Path(__file__).resolve().parents[2]
    staged_root = args.host_stage / "scripts/pilot"
    (staged_root / "agent_runtime").mkdir(mode=0o755, parents=True)
    for relative in (
        "scripts/pilot/agent-runtime-development-proxy.py",
        "scripts/pilot/agent_runtime/development_venus_proxy.py",
        "scripts/pilot/agent_runtime/development_proxy_mock_upstream.py",
    ):
        destination = args.host_stage / relative
        shutil.copyfile(source_root / relative, destination)
        destination.chmod(0o444)
    staged_source = args.host_stage / "models/agent-runtime/cup_cup_033/source"
    staged_input = args.host_stage / "models/agent-runtime/cup_cup_033/input"
    staged_source.mkdir(mode=0o755, parents=True)
    staged_input.mkdir(mode=0o755, parents=True)
    shutil.copyfile(
        source_root / "models/agent-runtime/cup_cup_033/source/cup_cup_033.implicit.js",
        staged_source / "cup_cup_033.implicit.js",
    )
    shutil.copyfile(
        source_root / "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply",
        staged_input / "cup_cup_033.ply",
    )
    client_token = secrets.token_urlsafe(32)
    upstream_token = secrets.token_urlsafe(32)

    internal = f"t2c-{args.suffix}-internal"
    egress = f"t2c-{args.suffix}-egress"
    mock = f"t2c-{args.suffix}-mock"
    slow_mock = f"t2c-{args.suffix}-slow-mock"
    proxy = f"t2c-{args.suffix}-proxy"
    slow_proxy = f"t2c-{args.suffix}-slow-proxy"
    evidence = f"t2c-{args.suffix}-evidence"
    slow_evidence = f"t2c-{args.suffix}-slow-evidence"
    secret_volume = f"t2c-{args.suffix}-secrets"
    containers = (proxy, slow_proxy, mock, slow_mock)
    volumes = (evidence, slow_evidence, secret_volume)
    networks = (internal, egress)
    events: list[dict[str, object]] = []
    status = 1
    stage = "admission"
    try:
        stage = "runtime-identity"
        image_observation = json.loads(run("image", "inspect", IMAGE).stdout)[0]
        if (
            image_observation.get("Id") != IMAGE
            or image_observation.get("Architecture") != "amd64"
            or image_observation.get("Os") != "linux"
        ):
            raise RuntimeError("imported Development image identity or platform drifted")
        if any(not absent("container", name) for name in containers):
            raise RuntimeError("exact conformance container name already exists")
        if any(not absent("network", name) for name in networks):
            raise RuntimeError("exact conformance network name already exists")
        if any(not absent("volume", name) for name in volumes):
            raise RuntimeError("exact conformance volume name already exists")
        stage = "network-create"
        run("network", "create", "--internal", internal)
        run("network", "create", egress)
        for volume in volumes:
            run("volume", "create", volume)
        stage = "evidence-volume-permissions"
        run(
            "run", "--rm", "--pull", "never", "--read-only", "--user", "0:0",
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,src={evidence},dst=/e0",
            "--mount", f"type=volume,src={slow_evidence},dst=/e1",
            "--mount", f"type=volume,src={secret_volume},dst=/e2",
            "--entrypoint", "/usr/bin/chmod", IMAGE, "0777", "/e0", "/e1", "/e2",
        )
        stage = "secret-volume-seed"
        seed = (
            "import json,pathlib,sys; v=json.load(sys.stdin); "
            "[(p.write_text(v[n],encoding='utf-8'),p.chmod(0o400)) "
            "for n in ('client-token','upstream-token') for p in (pathlib.Path('/secrets')/n,)]"
        )
        run(
            "run", "--rm", "--interactive", "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,src={secret_volume},dst=/secrets",
            "--entrypoint", "/usr/bin/python3.12", IMAGE, "-c", seed,
            input_bytes=json.dumps({"client-token": client_token, "upstream-token": upstream_token}).encode(),
        )
        stage = "mock-start"
        mock_id = run(
            "run", "--detach", "--name", mock, "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", egress, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,src={args.host_stage},dst=/opt/text-to-cad,readonly",
            "--entrypoint", "/usr/bin/python3.12", IMAGE,
            "/opt/text-to-cad/scripts/pilot/agent_runtime/development_proxy_mock_upstream.py",
        ).stdout.decode().strip()
        events.append({"event": "mock-start", "containerId": mock_id})
        stage = "proxy-create"
        proxy_id = run(
            "create", "--name", proxy, "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,src={args.host_stage},dst=/opt/text-to-cad,readonly",
            "--mount", f"type=volume,src={secret_volume},dst=/run/secrets,readonly",
            "--mount", f"type=volume,src={evidence},dst=/evidence",
            "--entrypoint", "/usr/bin/python3.12", IMAGE,
            "/opt/text-to-cad/scripts/pilot/agent-runtime-development-proxy.py",
            "--provider-free-mock", "--target", f"http://{mock}:9000/llmproxy/v1",
            "--job-id", "cup-cup-033-provider-free-48", "--ledger", "/evidence/job-ledger.jsonl",
            "--client-token-file", "/run/secrets/client-token", "--upstream-token-file", "/run/secrets/upstream-token",
            "--max-attempts", "48", "--max-request-bytes", "100", "--max-output-tokens", "1",
        ).stdout.decode().strip()
        run("network", "connect", egress, proxy)
        run("start", proxy)
        events.append({"event": "proxy-start", "containerId": proxy_id})
        stage = "agent-capability-supervisor"
        workload_path = args.host_stage / "workload.json"
        capability_check = (
            "import os,pathlib,stat; p=pathlib.Path(os.environ['HOME'])/'.codex'; "
            "f=p/'config.toml'; ps=p.stat(); fs=f.stat(); "
            "assert (ps.st_uid,ps.st_gid,stat.S_IMODE(ps.st_mode))==(65532,65532,0o700); "
            "assert (fs.st_uid,fs.st_gid,stat.S_IMODE(fs.st_mode))==(65532,65532,0o600); "
            "v=f.read_text(encoding='utf-8'); assert 'model_provider = \"venus\"' in v; "
            "assert 'wire_api = \"responses\"' in v; assert 'experimental_bearer_token' in v"
        )
        workload_path.write_bytes(json.dumps(
            ["/usr/bin/python3.12", "-c", capability_check],
            separators=(",", ":"),
        ).encode())
        agent_output = args.host_stage / "agent-capability-supervisor"
        agent_output.mkdir(mode=0o700)
        agent_request = fixed_candidate_request(
            repo_root=source_root,
            image_id=IMAGE,
            source_dir=staged_source,
            input_dir=staged_input,
            output_dir=agent_output,
            workload_path=workload_path,
            internal_network=internal,
            proxy_base_url=f"http://{proxy}:8080/v1",
            proxy_client_token=client_token,
        )
        agent_receipt = execute_agent(
            agent_request,
            engine=DockerEngine(DOCKER_EXECUTABLE, context=DOCKER_CONTEXT),
        )
        shutil.copytree(agent_output, args.output / "agent-capability-supervisor")
        events.append({
            "event": "agent-capability-supervisor-pass",
            "containerAbsent": agent_receipt["containerAbsent"],
            "ownerLabelsAbsent": agent_receipt["ownerLabelsAbsent"],
            "proxyCapability": agent_receipt["proxyCapability"],
            "providerAccounting": agent_receipt["providerAccounting"],
            "providerDispatchCount": agent_receipt["providerDispatchCount"],
        })
        probe = f'''
import http.client, json, time
token=open("/run/secrets/client-token",encoding="utf-8").read()
def request(path, body, capability=token):
    c=http.client.HTTPConnection({proxy!r},8080,timeout=3)
    c.request("POST",path,body=body,headers={{"Authorization":"Bearer "+capability,"Content-Type":"application/json"}})
    r=c.getresponse(); r.read(); c.close(); return r.status
for _ in range(60):
    try:
        c=http.client.HTTPConnection({proxy!r},8080,timeout=1); c.request("GET","/healthz"); r=c.getresponse(); r.read(); c.close()
        if r.status==200: break
    except OSError: time.sleep(.1)
else: raise SystemExit("proxy readiness failed")
body=b'{{"model":"gpt-5.6-sol","input":"x","max_output_tokens":1}}'
result={{"wrongToken":request("/v1/responses",body,"wrong-job-token"),"wrongRoute":request("/v1/chat/completions",body),"wrongModel":request("/v1/responses",b'{{"model":"other","input":"x"}}')}}
result["attemptStatuses"]=[request("/v1/responses",body) for _ in range(49)]
print(json.dumps(result,sort_keys=True,separators=(",",":")))
'''.encode()
        stage = "48-attempt-probe"
        probe_result = run(
            "run", "--rm", "--interactive", "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,src={secret_volume},dst=/run/secrets,readonly",
            "--entrypoint", "/usr/bin/python3.12", IMAGE, "-",
            input_bytes=probe,
        )
        result = json.loads(probe_result.stdout)
        if result != {"wrongToken": 401, "wrongRoute": 404, "wrongModel": 400, "attemptStatuses": [200] * 48 + [429]}:
            raise RuntimeError("48-attempt or denial conformance mismatch")
        stage = "48-attempt-terminal-copy"
        run("stop", "--time", "5", proxy)
        run("cp", f"{proxy}:/evidence/.", str(args.output / "48-attempt"))
        events.append({"event": "48-attempt-pass", **result})

        stage = "slow-mock-start"
        run(
            "run", "--detach", "--name", slow_mock, "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", egress, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,src={args.host_stage},dst=/opt/text-to-cad,readonly",
            "--entrypoint", "/usr/bin/python3.12", IMAGE,
            "/opt/text-to-cad/scripts/pilot/agent_runtime/development_proxy_mock_upstream.py", "--delay", "0.2",
        )
        stage = "slow-proxy-create"
        run(
            "create", "--name", slow_proxy, "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,src={args.host_stage},dst=/opt/text-to-cad,readonly",
            "--mount", f"type=volume,src={secret_volume},dst=/run/secrets,readonly",
            "--mount", f"type=volume,src={slow_evidence},dst=/evidence",
            "--entrypoint", "/usr/bin/python3.12", IMAGE,
            "/opt/text-to-cad/scripts/pilot/agent-runtime-development-proxy.py",
            "--provider-free-mock", "--target", f"http://{slow_mock}:9000/llmproxy/v1",
            "--job-id", "cup-cup-033-provider-free-timeout", "--ledger", "/evidence/job-ledger.jsonl",
            "--client-token-file", "/run/secrets/client-token", "--upstream-token-file", "/run/secrets/upstream-token",
            "--max-attempts", "1", "--max-request-bytes", "100", "--max-output-tokens", "1", "--upstream-timeout", "0.05",
        )
        run("network", "connect", egress, slow_proxy)
        run("start", slow_proxy)
        time.sleep(0.4)
        timeout_probe = f'''
import http.client,time
token=open("/run/secrets/client-token",encoding="utf-8").read()
for _ in range(60):
    try:
        c=http.client.HTTPConnection({slow_proxy!r},8080,timeout=1); c.request("GET","/healthz"); r=c.getresponse(); r.read(); c.close()
        if r.status==200: break
    except OSError: time.sleep(.1)
else: raise SystemExit("slow proxy readiness failed")
c=http.client.HTTPConnection({slow_proxy!r},8080,timeout=3)
c.request("POST","/v1/responses",body=b'{{"model":"gpt-5.6-sol","input":"x","max_output_tokens":1}}',headers={{"Authorization":"Bearer "+token,"Content-Type":"application/json"}})
r=c.getresponse(); r.read(); print(r.status)
'''.encode()
        stage = "timeout-probe"
        timeout_status = int(run(
            "run", "--rm", "--interactive", "--pull", "never", "--read-only",
            "--user", "65532:65532", "--network", internal, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=volume,src={secret_volume},dst=/run/secrets,readonly",
            "--entrypoint", "/usr/bin/python3.12", IMAGE, "-",
            input_bytes=timeout_probe,
        ).stdout)
        if timeout_status != 502:
            raise RuntimeError("timeout conformance mismatch")
        stage = "timeout-terminal-copy"
        run("stop", "--time", "5", slow_proxy)
        run("cp", f"{slow_proxy}:/evidence/.", str(args.output / "timeout"))
        events.append({"event": "timeout-pass", "status": timeout_status})
        status = 0
    except BaseException as error:
        failure: dict[str, object] = {
            "event": "failure", "stage": stage, "category": type(error).__name__,
        }
        if isinstance(error, subprocess.CalledProcessError):
            failure["returncode"] = error.returncode
        events.append(failure)
    finally:
        for name in (proxy, slow_proxy):
            run("stop", "--time", "2", name, check=False)
            destination = args.output / f"retained-{name}"
            destination.mkdir(exist_ok=True)
            run("cp", f"{name}:/evidence/.", str(destination), check=False)
        for name in containers:
            logs = run("logs", name, check=False)
            (args.output / f"{name}.stdout").write_bytes(logs.stdout)
            (args.output / f"{name}.stderr").write_bytes(logs.stderr)
        for name in containers:
            run("rm", "--force", name, check=False)
        for name in volumes:
            run("volume", "rm", "--force", name, check=False)
        for name in networks:
            run("network", "rm", name, check=False)
        cleanup = {
            "containersAbsent": all(absent("container", name) for name in containers),
            "volumesAbsent": all(absent("volume", name) for name in volumes),
            "networksAbsent": all(absent("network", name) for name in networks),
            "secretVolumeAbsent": absent("volume", secret_volume),
        }
        receipt = {
            "schema": "text-to-cad.development-venus-proxy-conformance/1",
            "classification": "Development/Not Sealed/Not Formal",
            "providerDispatchCount": 0,
            "dockerContext": DOCKER_CONTEXT,
            "runtimeProfile": "sealed-agent-runtime",
            "platform": {"architecture": "amd64", "os": "linux"},
            "imageId": IMAGE,
            "events": events,
            "cleanup": cleanup,
            "status": "pass" if status == 0 and all(cleanup.values()) else "fail",
        }
        (args.output / "conformance.json").write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if receipt["status"] != "pass":
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
