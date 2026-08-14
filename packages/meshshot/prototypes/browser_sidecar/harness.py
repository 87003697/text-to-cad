#!/usr/bin/env python3
"""THROWAWAY one-command P0-P3 Browser Sidecar evidence harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Any


PREFIX = "meshshot-sidecar-prototype-harness"
SIDECAR_TAG = "meshshot-sidecar-prototype:final"
AGENT_TAG = "meshshot-sidecar-agent-client-prototype:final"
LEGACY_TAG = "meshshot-sidecar-legacy-parity-prototype:final"
EXPECTED_SKIP_BUILD_IMAGES = {
    "sidecar": "sha256:c61318789e67cbac0ef1d4b0b91b25b158070cee5c516e296d9661097fa980fb",
    "agent": "sha256:d6af75274aebcdac805a3247af557a84740cf0a053c2575bca9faf8ebbafcd77",
    "legacy": "sha256:10335f887a051749c3074e7ec28628e9278d309e0a09cbf9f2d72efc78c14d95",
}


class HarnessError(RuntimeError):
    pass


class Harness:
    def __init__(self, *, docker_host: str, repo: Path, evidence_dir: Path) -> None:
        self.docker = ["docker", "--host", docker_host]
        self.repo = repo
        self.evidence_dir = evidence_dir
        self.commands: list[dict[str, Any]] = []

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess[str]:
        command = [*self.docker, *args]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        self.commands.append({
            "argv": command,
            "exitCode": completed.returncode,
            "elapsedSeconds": round(time.monotonic() - started, 3),
        })
        if check and completed.returncode:
            raise HarnessError(
                f"command failed ({completed.returncode}): {' '.join(command)}\n"
                f"{completed.stdout}\n{completed.stderr}"
            )
        return completed

    def image_digest(self, tag: str) -> str:
        output = self.run("image", "inspect", tag, "--format", "{{.Id}}").stdout.strip()
        if not output.startswith("sha256:"):
            raise HarnessError(f"invalid image digest for {tag}: {output}")
        return output

    def build(self) -> dict[str, str]:
        dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile"
        agent_dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile.agent"
        legacy_dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile.legacy"
        self.run("build", "--platform", "linux/amd64", "--pull=false", "-f", dockerfile, "-t", SIDECAR_TAG, ".", timeout=3600)
        self.run("build", "--platform", "linux/amd64", "--pull=false", "-f", agent_dockerfile, "-t", AGENT_TAG, ".", timeout=1800)
        sidecar = self.image_digest(SIDECAR_TAG)
        self.run(
            "build", "--platform", "linux/amd64", "--pull=false",
            "--build-arg", f"SIDECAR_IMAGE={SIDECAR_TAG}@{sidecar}",
            "-f", legacy_dockerfile, "-t", LEGACY_TAG, ".", timeout=1200,
        )
        return {
            "sidecar": sidecar,
            "agent": self.image_digest(AGENT_TAG),
            "legacy": self.image_digest(LEGACY_TAG),
        }

    def start_job(self, suffix: str, *, cpus: str = "1.5", memory: str = "1536m") -> dict[str, str]:
        job_id = suffix.replace("_", "-")
        network = f"{PREFIX}-{suffix}"
        container = f"{network}-sidecar"
        self.run("network", "create", "--internal", network)
        self.run(
            "run", "-d", "--name", container,
            "--network", network, "--network-alias", "sidecar",
            "--pull", "never", "--platform", "linux/amd64",
            "--read-only", "--init", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "256", "--memory", memory, "--memory-swap", memory,
            "--cpus", cpus, "--shm-size", "256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
            "-e", f"BROWSER_SIDECAR_JOB_ID={job_id}", SIDECAR_TAG,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            logs = self.run("logs", container, check=False, timeout=30).stdout
            if '"event":"ready"' in logs:
                return {"jobId": job_id, "network": network, "container": container, "logs": logs}
            time.sleep(1)
        raise HarnessError(f"sidecar did not become ready: {container}")

    def run_client(self, job: dict[str, str], program: str, *, detached: bool = False) -> Any:
        name = f"{job['network']}-client"
        command = [
            *self.docker, "run", "--rm", "--name", name,
            "--network", job["network"], "--pull", "never", "--platform", "linux/amd64",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--memory", "768m", "--memory-swap", "768m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=8m,uid=1001,gid=1001,mode=700",
            "-e", f"BROWSER_SIDECAR_JOB_ID={job['jobId']}",
            "-e", "BROWSER_SIDECAR_HOST=sidecar", AGENT_TAG, program,
        ]
        if detached:
            return name, subprocess.Popen(command, cwd=self.repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started = time.monotonic()
        completed = subprocess.run(command, cwd=self.repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        self.commands.append({"argv": command, "exitCode": completed.returncode, "elapsedSeconds": round(time.monotonic() - started, 3)})
        if completed.returncode:
            raise HarnessError(completed.stderr or completed.stdout)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def stop_job(self, job: dict[str, str], *, remove_network: bool = True) -> dict[str, Any]:
        self.run("stop", "--timeout", "15", job["container"], check=False)
        logs = self.run("logs", job["container"], check=False).stdout
        state = json.loads(self.run("inspect", job["container"], "--format", "{{json .State}}").stdout)
        self.run("rm", job["container"], check=False)
        if remove_network:
            self.run("network", "rm", job["network"], check=False)
        return {"logs": logs, "state": state}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    harness = Harness(docker_host=args.docker_host, repo=repo, evidence_dir=args.evidence_dir)
    evidence: dict[str, Any] = {
        "schema": "meshshot.browser-sidecar.prototype-evidence/1",
        "startedAtUnix": time.time(),
        "baseRevision": "9c5b7ea39030a013023a2f06c83b9b869a394861",
        "playwrightBaseAmd64Digest": "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9",
    }
    jobs: list[dict[str, str]] = []
    try:
        evidence["images"] = harness.build() if not args.skip_build else {
            "sidecar": harness.image_digest(SIDECAR_TAG),
            "agent": harness.image_digest(AGENT_TAG),
            "legacy": harness.image_digest(LEGACY_TAG),
        }
        if args.skip_build and evidence["images"] != EXPECTED_SKIP_BUILD_IMAGES:
            raise HarnessError(
                "--skip-build requires the exact reviewed image IDs: "
                f"expected {EXPECTED_SKIP_BUILD_IMAGES}, got {evidence['images']}"
            )

        suite = harness.start_job("suite")
        jobs.append(suite)
        inspect = json.loads(harness.run("inspect", suite["container"]).stdout)[0]
        network = json.loads(harness.run("network", "inspect", suite["network"]).stdout)[0]
        suite_result = harness.run_client(suite, "suite")
        suite_terminal = harness.stop_job(suite)
        jobs.remove(suite)
        evidence["p1"] = {
            "readonlyRootfs": inspect["HostConfig"]["ReadonlyRootfs"],
            "mounts": inspect["Mounts"],
            "tmpfs": inspect["HostConfig"]["Tmpfs"],
            "shmSize": inspect["HostConfig"]["ShmSize"],
            "memory": inspect["HostConfig"]["Memory"],
            "memorySwap": inspect["HostConfig"]["MemorySwap"],
            "nanoCpus": inspect["HostConfig"]["NanoCpus"],
            "pidsLimit": inspect["HostConfig"]["PidsLimit"],
            "networkInternal": network["Internal"],
            "probe": suite_result["result"]["probe"],
            "terminal": suite_terminal,
        }
        evidence["p2"] = suite_result["result"]

        legacy = json.loads(harness.run(
            "run", "--rm", "--name", f"{PREFIX}-legacy", "--network", "none",
            "--pull", "never", "--platform", "linux/amd64", "--read-only", "--init",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "256", "--memory", "1536m", "--memory-swap", "1536m",
            "--cpus", "1.5", "--shm-size", "256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
            LEGACY_TAG, timeout=600,
        ).stdout.strip().splitlines()[-1])
        remote = suite_result["result"]["residual"]
        evidence["p2"]["legacyParity"] = {
            "legacy": legacy,
            "pngBytesEqual": legacy["pngBytes"] == remote["pngBytes"],
            "pngSha256Equal": legacy["pngSha256"] == remote["pngSha256"],
            "profileSha256Equal": legacy["profileSha256"] == remote["profileSha256"],
            "viewOrderEqual": legacy["views"] == [view["name"] for view in remote["views"]],
        }

        job_a = harness.start_job("job-a", cpus="0.75", memory="1g")
        job_b = harness.start_job("job-b", cpus="0.75", memory="1g")
        jobs.extend([job_a, job_b])
        name_a, client_a = harness.run_client(job_a, "hold", detached=True)
        name_b, client_b = harness.run_client(job_b, "hold", detached=True)
        if client_a.stdout is None or client_b.stdout is None:
            raise HarnessError("detached client stdout was not captured")
        ready_a = json.loads(client_a.stdout.readline().strip())
        ready_b = json.loads(client_b.stdout.readline().strip())
        if ready_a.get("event") != "hold-ready" or ready_b.get("event") != "hold-ready":
            raise HarnessError(f"clients did not enter hold: {ready_a!r}, {ready_b!r}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            names = harness.run("ps", "--format", "{{.Names}}").stdout.splitlines()
            if name_a in names and name_b in names:
                break
            time.sleep(1)
        top_a = harness.run("top", name_a, "-eo", "pid,ppid,user,comm,args").stdout
        top_b = harness.run("top", name_b, "-eo", "pid,ppid,user,comm,args").stdout
        terminal_a = harness.stop_job(job_a, remove_network=False)
        jobs.remove(job_a)
        stdout_a, stderr_a = client_a.communicate(timeout=120)
        stdout_b, stderr_b = client_b.communicate(timeout=120)
        harness.run("network", "rm", job_a["network"], check=False)
        state_b_before_stop = json.loads(harness.run("inspect", job_b["container"], "--format", "{{json .State}}").stdout)
        terminal_b = harness.stop_job(job_b)
        jobs.remove(job_b)
        evidence["p3"] = {
            "agentTopA": top_a,
            "agentTopB": top_b,
            "holdReadyA": ready_a,
            "holdReadyB": ready_b,
            "clientA": {"exitCode": client_a.returncode, "stdout": stdout_a, "stderr": stderr_a},
            "clientB": {"exitCode": client_b.returncode, "stdout": stdout_b, "stderr": stderr_b},
            "jobBRunningAfterJobACancel": state_b_before_stop["Running"],
            "terminalA": terminal_a,
            "terminalB": terminal_b,
        }
        evidence["residue"] = {
            "containers": harness.run("ps", "-a", "--filter", f"name={PREFIX}", "--format", "{{.Names}}").stdout.splitlines(),
            "networks": harness.run("network", "ls", "--filter", f"name={PREFIX}", "--format", "{{.Name}}").stdout.splitlines(),
        }
        evidence["verdict"] = "ADOPT" if all([
            evidence["p1"]["readonlyRootfs"],
            evidence["p1"]["mounts"] == [],
            evidence["p1"]["networkInternal"],
            evidence["p1"]["probe"]["externalEgressBlocked"],
            evidence["p1"]["probe"]["browserExecutablesVisible"] == [],
            evidence["p2"]["legacyParity"]["pngSha256Equal"],
            evidence["p3"]["clientA"]["exitCode"] != 0,
            evidence["p3"]["clientB"]["exitCode"] == 0,
            evidence["p3"]["jobBRunningAfterJobACancel"],
            evidence["residue"]["containers"] == [],
            evidence["residue"]["networks"] == [],
        ]) else "REJECT"
    finally:
        for job in reversed(jobs):
            harness.stop_job(job)
        evidence["commands"] = harness.commands
        evidence["finishedAtUnix"] = time.time()
        output = args.evidence_dir / "evidence.json"
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf8")
        print(output)
    return 0 if evidence.get("verdict") == "ADOPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
