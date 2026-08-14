#!/usr/bin/env python3
"""THROWAWAY one-command P0-P3 Browser Sidecar evidence harness."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time
from typing import Any


PREFIX = "meshshot-sidecar-prototype-harness"
SIDECAR_TAG = "meshshot-sidecar-prototype:final"
AGENT_TAG = "meshshot-sidecar-agent-client-prototype:final"
LEGACY_TAG = "meshshot-sidecar-legacy-parity-prototype:final"
OWNERSHIP_LABEL = "io.text-to-cad.prototype-harness-owner"
RESOURCE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class HarnessError(RuntimeError):
    pass


class HarnessInterrupted(HarnessError):
    pass


@dataclass
class InterruptState:
    signal_name: str | None = None
    cleanup_started: bool = False

    def handle(self, signum: int, _frame: Any) -> None:
        self.signal_name = signal.Signals(signum).name
        if not self.cleanup_started:
            raise HarnessInterrupted(f"received {self.signal_name}")


@dataclass
class ResourceRef:
    kind: str
    name: str
    resource_id: str


@dataclass
class DetachedRun:
    name: str
    container_id: str
    command: list[str]
    process: subprocess.Popen[str]
    started: float
    finished: bool = False


class ExactResourceLedger:
    def __init__(self) -> None:
        self.containers: list[ResourceRef] = []
        self.networks: list[ResourceRef] = []

    @staticmethod
    def _register(target: list[ResourceRef], *, kind: str, name: str, resource_id: str) -> None:
        if not resource_id or any(item.resource_id == resource_id for item in target):
            raise HarnessError(f"invalid or duplicate {kind} identity: {resource_id!r}")
        target.append(ResourceRef(kind=kind, name=name, resource_id=resource_id))

    def register_container(self, *, name: str, resource_id: str) -> None:
        self._register(self.containers, kind="container", name=name, resource_id=resource_id)

    def register_network(self, *, name: str, resource_id: str) -> None:
        self._register(self.networks, kind="network", name=name, resource_id=resource_id)


class Harness:
    def __init__(self, *, docker_host: str, repo: Path, evidence_dir: Path) -> None:
        self.docker = ["docker", "--host", docker_host]
        self.repo = repo
        self.evidence_dir = evidence_dir
        self.commands: list[dict[str, Any]] = []
        self.ledger = ExactResourceLedger()
        self.detached_runs: list[DetachedRun] = []
        self.ownership_token = secrets.token_hex(16)
        self.pending_container_names: set[str] = set()
        self.pending_network_names: set[str] = set()
        self.source_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()

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

    def image_metadata(self, tag: str) -> dict[str, Any]:
        inspected = json.loads(self.run("image", "inspect", tag).stdout)[0]
        return {
            "id": inspected["Id"],
            "os": inspected["Os"],
            "architecture": inspected["Architecture"],
            "labels": inspected["Config"].get("Labels") or {},
        }

    def verify_clean_source(self) -> None:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if completed.returncode:
            raise HarnessError(f"git status failed: {completed.stderr.strip()}")
        if completed.stdout:
            raise HarnessError(
                "default build requires a clean tracked and untracked source tree:\n"
                f"{completed.stdout}"
            )

    def _inspect_owned_resource(self, kind: str, name: str) -> tuple[str, str | None]:
        inspected = self.run(kind, "inspect", name, check=False, timeout=30)
        if inspected.returncode:
            return "missing", None
        try:
            record = json.loads(inspected.stdout)[0]
            resource_id = record["Id"]
            actual_name = record["Name"]
            if kind == "container":
                labels = record["Config"].get("Labels") or {}
            else:
                labels = record.get("Labels") or {}
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HarnessError(f"invalid {kind} identity receipt for {name}: {exc}") from exc
        expected_name = f"/{name}" if kind == "container" else name
        if not isinstance(resource_id, str) or not RESOURCE_ID_RE.fullmatch(resource_id):
            raise HarnessError(f"invalid {kind} ID for {name}: {resource_id!r}")
        if actual_name != expected_name or labels.get(OWNERSHIP_LABEL) != self.ownership_token:
            return "foreign", resource_id
        return "owned", resource_id

    def _create_owned_resource(self, kind: str, name: str, *args: str) -> str:
        pending = self.pending_container_names if kind == "container" else self.pending_network_names
        pending.add(name)
        create_error: Exception | None = None
        completed: subprocess.CompletedProcess[str] | None = None
        command = (
            ("create", "--name", name, "--label", f"{OWNERSHIP_LABEL}={self.ownership_token}", *args)
            if kind == "container"
            else ("network", "create", "--label", f"{OWNERSHIP_LABEL}={self.ownership_token}", *args, name)
        )
        try:
            completed = self.run(*command, check=False)
        except Exception as exc:
            create_error = exc

        status, resource_id = self._inspect_owned_resource(kind, name)
        if status == "foreign":
            pending.discard(name)
            raise HarnessError(f"refusing to adopt foreign {kind} with deterministic name {name}")
        if status == "missing" or resource_id is None:
            pending.discard(name)
            if create_error is not None:
                raise HarnessError(f"{kind} create failed before owned identity recovery: {create_error}") from create_error
            assert completed is not None
            raise HarnessError(
                f"{kind} create did not yield an owned resource ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )

        output_id = completed.stdout.strip() if completed is not None else ""
        if output_id and output_id != resource_id:
            raise HarnessError(
                f"{kind} create/inspect identity mismatch for {name}: {output_id!r} != {resource_id!r}"
            )
        if kind == "container":
            self.ledger.register_container(name=name, resource_id=resource_id)
        else:
            self.ledger.register_network(name=name, resource_id=resource_id)
        pending.discard(name)
        return resource_id

    def create_network(self, name: str, *args: str) -> str:
        return self._create_owned_resource("network", name, *args)

    def cleanup_all(self) -> dict[str, Any]:
        failures: list[dict[str, Any]] = []

        def attempt(
            kind: str,
            resource_id: str,
            *args: str,
            record_nonzero: bool = True,
        ) -> subprocess.CompletedProcess[str] | None:
            try:
                completed = self.run(*args, check=False)
            except Exception as exc:
                failures.append({
                    "kind": kind,
                    "id": resource_id,
                    "argv": list(args),
                    "exitCode": None,
                    "stderr": str(exc),
                })
                return None
            if record_nonzero and completed.returncode:
                failures.append({"kind": kind, "id": resource_id, "argv": list(args), "exitCode": completed.returncode, "stderr": completed.stderr.strip()})
            return completed

        def recover_pending(kind: str, names: set[str]) -> None:
            for name in sorted(tuple(names)):
                try:
                    status, resource_id = self._inspect_owned_resource(kind, name)
                except Exception as exc:
                    failures.append({
                        "kind": kind,
                        "id": name,
                        "argv": [kind, "inspect", name],
                        "exitCode": None,
                        "stderr": str(exc),
                    })
                    continue
                if status == "owned" and resource_id is not None:
                    try:
                        if kind == "container":
                            self.ledger.register_container(name=name, resource_id=resource_id)
                        else:
                            self.ledger.register_network(name=name, resource_id=resource_id)
                    except Exception as exc:
                        failures.append({
                            "kind": kind,
                            "id": resource_id,
                            "argv": ["ledger", "register", kind, name],
                            "exitCode": None,
                            "stderr": str(exc),
                        })
                        continue
                    names.discard(name)
                elif status == "missing":
                    names.discard(name)
                else:
                    failures.append({
                        "kind": kind,
                        "id": resource_id or name,
                        "argv": [kind, "inspect", name],
                        "exitCode": None,
                        "stderr": f"refusing to adopt foreign {kind} named {name}",
                    })
                    names.discard(name)

        recover_pending("container", self.pending_container_names)
        recover_pending("network", self.pending_network_names)

        for item in reversed(self.ledger.containers):
            attempt(
                item.kind,
                item.resource_id,
                "container", "inspect", item.resource_id, "--format", "{{.State.Running}}",
                record_nonzero=False,
            )
            attempt(
                item.kind,
                item.resource_id,
                "stop", item.resource_id,
                record_nonzero=False,
            )
            attempt(item.kind, item.resource_id, "rm", item.resource_id)
        for item in reversed(self.ledger.networks):
            attempt(item.kind, item.resource_id, "network", "rm", item.resource_id)

        for detached in self.detached_runs:
            if detached.finished:
                continue
            try:
                detached.process.communicate(timeout=30)
                detached.finished = True
                self.commands.append({"argv": detached.command, "exitCode": detached.process.returncode, "elapsedSeconds": round(time.monotonic() - detached.started, 3)})
            except Exception as exc:
                failures.append({"kind": "detached-process", "id": detached.container_id, "argv": detached.command, "exitCode": None, "stderr": str(exc)})
                try:
                    detached.process.terminate()
                except Exception as terminate_exc:
                    failures.append({"kind": "detached-process", "id": detached.container_id, "argv": [*detached.command, "<terminate>"], "exitCode": None, "stderr": str(terminate_exc)})
                try:
                    detached.process.communicate(timeout=5)
                    detached.finished = True
                except Exception as terminate_wait_exc:
                    failures.append({"kind": "detached-process", "id": detached.container_id, "argv": [*detached.command, "<terminate-wait>"], "exitCode": None, "stderr": str(terminate_wait_exc)})
                    try:
                        detached.process.kill()
                    except Exception as kill_exc:
                        failures.append({"kind": "detached-process", "id": detached.container_id, "argv": [*detached.command, "<kill>"], "exitCode": None, "stderr": str(kill_exc)})
                    try:
                        detached.process.communicate(timeout=5)
                        detached.finished = True
                    except Exception as reap_exc:
                        failures.append({"kind": "detached-process", "id": detached.container_id, "argv": [*detached.command, "<kill-wait>"], "exitCode": None, "stderr": str(reap_exc)})
                if detached.finished:
                    self.commands.append({"argv": detached.command, "exitCode": detached.process.returncode, "elapsedSeconds": round(time.monotonic() - detached.started, 3)})

        absence_proofs: list[dict[str, Any]] = []
        for item in self.ledger.containers:
            inspected = attempt(
                item.kind,
                item.resource_id,
                "container", "inspect", item.resource_id,
                record_nonzero=False,
            )
            absence_proofs.append({
                "kind": item.kind,
                "name": item.name,
                "id": item.resource_id,
                "absent": inspected is not None and inspected.returncode != 0,
                "inspectionError": inspected is None,
            })
        for item in self.ledger.networks:
            inspected = attempt(
                item.kind,
                item.resource_id,
                "network", "inspect", item.resource_id,
                record_nonzero=False,
            )
            absence_proofs.append({
                "kind": item.kind,
                "name": item.name,
                "id": item.resource_id,
                "absent": inspected is not None and inspected.returncode != 0,
                "inspectionError": inspected is None,
            })
        return {
            "resources": [vars(item) for item in [*self.ledger.containers, *self.ledger.networks]],
            "failures": failures,
            "firstFailure": failures[0] if failures else None,
            "absenceProofs": absence_proofs,
            "pendingNames": {
                "containers": sorted(self.pending_container_names),
                "networks": sorted(self.pending_network_names),
            },
        }

    def build(self) -> dict[str, str]:
        self.verify_clean_source()
        dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile"
        agent_dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile.agent"
        legacy_dockerfile = "packages/meshshot/prototypes/browser_sidecar/Dockerfile.legacy"
        revision = ("--build-arg", f"PROTOTYPE_SOURCE_REVISION={self.source_revision}")
        self.run("build", "--platform", "linux/amd64", "--pull=false", *revision, "-f", dockerfile, "-t", SIDECAR_TAG, ".", timeout=3600)
        self.run("build", "--platform", "linux/amd64", "--pull=false", *revision, "-f", agent_dockerfile, "-t", AGENT_TAG, ".", timeout=1800)
        sidecar = self.image_digest(SIDECAR_TAG)
        self.run(
            "build", "--platform", "linux/amd64", "--pull=false",
            "--build-arg", f"SIDECAR_IMAGE={SIDECAR_TAG}@{sidecar}",
            *revision,
            "-f", legacy_dockerfile, "-t", LEGACY_TAG, ".", timeout=1200,
        )
        return {
            "sidecar": sidecar,
            "agent": self.image_digest(AGENT_TAG),
            "legacy": self.image_digest(LEGACY_TAG),
        }

    def run_public_parity(self, name: str, request: dict[str, Any], *, mode: str, job: dict[str, str] | None = None) -> dict[str, Any]:
        if mode not in {"baseline", "remote"} or (mode == "remote") != (job is not None):
            raise HarnessError("public parity mode/job mismatch")
        network_args = ("--network", "none") if job is None else ("--network", job["networkId"])
        entrypoint_args: tuple[str, ...] = ()
        image_and_command: tuple[str, ...] = (LEGACY_TAG,)
        environment: tuple[str, ...] = ("-e", "PYTHONDONTWRITEBYTECODE=1")
        if job is not None:
            entrypoint_args = ("--entrypoint", "python3")
            image_and_command = (AGENT_TAG, "/opt/browser-sidecar/prototype/public_parity.py", "remote")
            environment += (
                "-e", f"BROWSER_SIDECAR_JOB_ID={job['jobId']}",
                "-e", "BROWSER_SIDECAR_HOST=sidecar",
            )
        container_id = self.create_container(
            name,
            "-i", *network_args, "--pull", "never", "--platform", "linux/amd64",
            "--read-only", "--init", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "256", "--memory", "1536m", "--memory-swap", "1536m", "--cpus", "1.5", "--shm-size", "256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
            *environment, *entrypoint_args, *image_and_command,
        )
        completed = self.start_attached(container_id, request, timeout=600)
        if completed.returncode:
            raise HarnessError(completed.stderr or completed.stdout)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def start_job(self, suffix: str, *, cpus: str = "1.5", memory: str = "1536m") -> dict[str, str]:
        job_id = suffix.replace("_", "-")
        network = f"{PREFIX}-{suffix}"
        container = f"{network}-sidecar"
        network_id = self.create_network(network, "--internal")
        container_id = self.create_container(
            container,
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
        self.run("start", container_id)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            logs = self.run("logs", container_id, check=False, timeout=30).stdout
            if '"event":"ready"' in logs:
                return {"jobId": job_id, "network": network, "networkId": network_id, "container": container, "containerId": container_id, "logs": logs}
            time.sleep(1)
        raise HarnessError(f"sidecar did not become ready: {container}")

    def create_container(self, name: str, *args: str) -> str:
        return self._create_owned_resource("container", name, *args)

    def start_attached(self, container_id: str, request: dict[str, Any], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        command = [*self.docker, "start", "-a", "-i", container_id]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=self.repo,
            input=json.dumps(request, sort_keys=True, separators=(",", ":")),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        self.commands.append({"argv": command, "exitCode": completed.returncode, "elapsedSeconds": round(time.monotonic() - started, 3)})
        return completed

    def start_detached(self, name: str, container_id: str, request: dict[str, Any]) -> DetachedRun:
        command = [*self.docker, "start", "-a", "-i", container_id]
        process = subprocess.Popen(
            command,
            cwd=self.repo,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        detached = DetachedRun(name=name, container_id=container_id, command=command, process=process, started=time.monotonic())
        self.detached_runs.append(detached)
        if process.stdin is None:
            raise HarnessError("detached client stdin was not captured")
        write_error: Exception | None = None
        try:
            process.stdin.write(json.dumps(request, sort_keys=True, separators=(",", ":")))
        except Exception as exc:
            write_error = exc
        finally:
            try:
                process.stdin.close()
            except Exception:
                if write_error is None:
                    raise
            finally:
                process.stdin = None
        if write_error is not None:
            raise write_error
        return detached

    def finish_detached(self, run: DetachedRun, *, timeout: int = 120) -> tuple[str, str]:
        stdout, stderr = run.process.communicate(timeout=timeout)
        self.commands.append({"argv": run.command, "exitCode": run.process.returncode, "elapsedSeconds": round(time.monotonic() - run.started, 3)})
        run.finished = True
        return stdout, stderr

    def run_client(self, job: dict[str, str], request: dict[str, Any], *, detached: bool = False) -> Any:
        name = f"{job['network']}-client"
        container_id = self.create_container(
            name,
            "-i", "--network", job["networkId"], "--pull", "never", "--platform", "linux/amd64",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--memory", "768m", "--memory-swap", "768m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=8m,uid=1001,gid=1001,mode=700",
            "-e", f"BROWSER_SIDECAR_JOB_ID={job['jobId']}",
            "-e", "BROWSER_SIDECAR_HOST=sidecar", AGENT_TAG,
        )
        if detached:
            return self.start_detached(name, container_id, request)
        completed = self.start_attached(container_id, request)
        if completed.returncode:
            raise HarnessError(completed.stderr or completed.stdout)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def stop_job(self, job: dict[str, str], *, remove_network: bool = True) -> dict[str, Any]:
        del remove_network
        self.run("stop", "--timeout", "15", job["containerId"])
        logs = self.run("logs", job["containerId"], check=False).stdout
        state = json.loads(self.run("inspect", job["containerId"], "--format", "{{json .State}}").stdout)
        return {"logs": logs, "state": state}


def render_request(program: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "meshshot.browser-sidecar.render-request/2",
        "program": program,
        "payload": payload,
    }


def residual_payload() -> dict[str, Any]:
    return {
        "reference": {
            "vertices": [[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
            "faces": [[0, 1, 2]],
        },
        "candidate": {
            "vertices": [[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
            "faces": [[0, 1, 2]],
        },
        "variant": "step",
        "exteriorDirections": [],
        "options": {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True},
    }


def hold_request(own: str, peer: str, *, two_triangles: bool) -> dict[str, Any]:
    vertices = [[-0.4, -0.3, 0.0], [0.4, -0.3, 0.0], [0.0, 0.4, 0.0]]
    faces = [[0, 1, 2]]
    if two_triangles:
        vertices += [[-0.3, -0.1, 0.2], [0.3, -0.1, 0.2], [0.0, 0.3, 0.2]]
        faces.append([3, 4, 5])
    return render_request("hold", {
        "model": {"vertices": vertices, "faces": faces},
        "ownMarker": own,
        "peerMarker": peer,
    })


def validate_image_revisions(
    images: dict[str, dict[str, Any]],
    expected_revisions: dict[str, str],
) -> None:
    expected_names = {"sidecar", "agent", "legacy"}
    if set(images) != expected_names or set(expected_revisions) != expected_names:
        raise HarnessError("image revision validation requires exact sidecar/agent/legacy keys")
    for name in sorted(expected_names):
        revision = expected_revisions[name]
        if not REVISION_RE.fullmatch(revision):
            raise HarnessError(f"invalid expected {name} source revision: {revision!r}")
        actual = images[name].get("labels", {}).get("org.opencontainers.image.revision")
        if actual != revision:
            raise HarnessError(
                f"{name} OCI source revision mismatch: expected {revision}, got {actual}"
            )


def predicate_matrix(evidence: dict[str, Any]) -> dict[str, bool]:
    def get(*path: str, default: Any = None) -> Any:
        value: Any = evidence
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    images = get("p0", "images", default={})
    expected_revisions = get("p0", "expectedRevisions", default={})
    labels_ok = bool(images) and set(images) == set(expected_revisions) and all(
        metadata.get("labels", {}).get("org.opencontainers.image.revision") == expected_revisions[name]
        and metadata.get("labels", {}).get("io.text-to-cad.source-base") == get("baseRevision")
        and metadata.get("labels", {}).get("io.text-to-cad.review-parent") == "629eaec232ab2816466dafb5182a1bb4fe66295d"
        for name, metadata in images.items()
    )
    p1_terminal = get("p1", "terminal", default={})
    viewer = get("p2", "nodeSuite", "result", "viewer", default={})
    residual = get("p2", "nodeSuite", "result", "residual", default={})
    parity = get("p2", "publicParity", default={})
    ready_a = get("p3", "holdReadyA", default={})
    ready_b = get("p3", "holdReadyB", default={})
    isolation_a = ready_a.get("isolation", {}) if isinstance(ready_a, dict) else {}
    isolation_b = ready_b.get("isolation", {}) if isinstance(ready_b, dict) else {}
    isolation_fields = (
        "pageOwn", "pagePeerAbsent", "localStorageOwn", "localStoragePeerAbsent",
        "cookieOwn", "cookiePeerAbsent", "consoleOwn", "consolePeerAbsent",
        "filesystemOwn", "filesystemPeerAbsent",
    )
    cleanup = get("cleanup", default={})
    matrix = {
        "p0.images_are_linux_amd64_digests": bool(images) and all(
            metadata.get("id", "").startswith("sha256:")
            and metadata.get("os") == "linux"
            and metadata.get("architecture") == "amd64"
            for metadata in images.values()
        ),
        "p0.labels_bind_clean_source_base_and_review_parent": labels_ok,
        "p1.readonly_root": get("p1", "readonlyRootfs") is True,
        "p1.no_mounts": get("p1", "mounts") == [],
        "p1.internal_network": get("p1", "networkInternal") is True,
        "p1.no_host_ports": get("p1", "portBindings") in ({}, None),
        "p1.non_root_pwuser": get("p1", "user") == "pwuser",
        "p1.capabilities_dropped": "ALL" in (get("p1", "capDrop", default=[]) or []),
        "p1.no_new_privileges": "no-new-privileges:true" in (get("p1", "securityOpt", default=[]) or []),
        "p1.bounded_tmp_profile": get("p1", "tmpfs") == {
            "/tmp": "rw,nosuid,nodev,size=128m,mode=1777",
            "/home/pwuser": "rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
        },
        "p1.bounded_shm_memory_cpu_pids": (
            get("p1", "shmSize") == 268_435_456
            and get("p1", "memory") == 1_610_612_736
            and get("p1", "memorySwap") == 1_610_612_736
            and get("p1", "nanoCpus") == 1_500_000_000
            and get("p1", "pidsLimit") == 256
        ),
        "p1.agent_connected_one_fresh_page": (
            get("p1", "probe", "connected") is True
            and get("p1", "probe", "contextCount") == 1
            and get("p1", "probe", "pageCount") == 1
        ),
        "p1.agent_has_no_browser_or_source_alias": (
            get("p1", "probe", "browserExecutablesVisible") == []
            and get("p1", "probe", "sourceAliasesVisible") == []
        ),
        "p1.external_egress_blocked": get("p1", "probe", "externalEgressBlocked") is True,
        "p1.sidecar_terminal_sigterm_exit_zero": (
            p1_terminal.get("state", {}).get("ExitCode") == 0
            and p1_terminal.get("state", {}).get("OOMKilled") is False
            and p1_terminal.get("state", {}).get("Status") == "exited"
            and '"event":"closing"' in p1_terminal.get("logs", "")
            and '"reason":"SIGTERM"' in p1_terminal.get("logs", "")
        ),
        "p2.structured_request_acknowledged": (
            get("p2", "nodeSuite", "schema") == "meshshot.browser-sidecar.render-request/2"
            and isinstance(get("p2", "nodeSuite", "requestSha256"), str)
        ),
        "p2.viewer_fixture_and_inspection_control": (
            viewer.get("title") == "CAD Viewer | browser_sidecar_inspection.step"
            and viewer.get("bodyMentionsFixture") is True
            and viewer.get("bodyHasArtifactError") is False
            and viewer.get("modelKey") == "inspection-step"
            and viewer.get("inspection", {}).get("changed") is True
        ),
        "p2.residual_fixed_geometry_variant_options": (
            residual.get("requestOptions") == {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True}
            and len(residual.get("views", [])) == 8
        ),
        "p2.public_baseline_and_remote_called": (
            get("p2", "baselinePublic", "publicCallable") == "meshshot.render_residual_preview"
            and get("p2", "remotePublic", "publicCallable") == "meshshot.render_residual_preview"
            and get("p2", "baselinePublic", "renderedType") == "RenderedPreview"
            and get("p2", "remotePublic", "renderedType") == "RenderedPreview"
        ),
        "p2.final_public_png_profile_views_evidence_parity": bool(parity) and all(parity.values()),
        "p3.both_jobs_reached_hold": ready_a.get("event") == "hold-ready" and ready_b.get("event") == "hold-ready",
        "p3.distinct_model_outputs": (
            isolation_a.get("modelInputSha256") != isolation_b.get("modelInputSha256")
            and isolation_a.get("modelPngSha256") != isolation_b.get("modelPngSha256")
        ),
        "p3.all_negative_cross_checks": all(isolation_a.get(key) is True and isolation_b.get(key) is True for key in isolation_fields),
        "p3.agent_processes_have_no_browser": all(
            token not in (get("p3", key, default="") or "").lower()
            for key in ("agentTopA", "agentTopB") for token in ("chrome", "chromium")
        ),
        "p3.cancel_a_does_not_cancel_b": (
            get("p3", "clientA", "exitCode") != 0
            and "Browser closed" in (get("p3", "clientA", "stderr", default="") or "")
            and get("p3", "clientB", "exitCode") == 0
            and get("p3", "jobBRunningAfterJobACancel") is True
        ),
        "p3.sidecars_terminal_exit_zero": (
            get("p3", "terminalA", "state", "ExitCode") == 0
            and get("p3", "terminalB", "state", "ExitCode") == 0
        ),
        "terminal.cleanup_has_no_failures": cleanup.get("failures") == [],
        "terminal.not_interrupted": get("interruptionSignal") is None,
        "terminal.every_exact_resource_absent": bool(cleanup.get("absenceProofs")) and all(
            proof.get("absent") is True for proof in cleanup.get("absenceProofs", [])
        ),
        "terminal.no_named_residue": get("residue") == {"containers": [], "networks": []},
    }
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--expected-sidecar-id")
    parser.add_argument("--expected-agent-id")
    parser.add_argument("--expected-legacy-id")
    parser.add_argument("--expected-sidecar-revision")
    parser.add_argument("--expected-agent-revision")
    parser.add_argument("--expected-legacy-revision")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    harness = Harness(docker_host=args.docker_host, repo=repo, evidence_dir=args.evidence_dir)
    evidence: dict[str, Any] = {
        "schema": "meshshot.browser-sidecar.prototype-evidence/2",
        "startedAtUnix": time.time(),
        "sourceRevision": harness.source_revision,
        "baseRevision": "9c5b7ea39030a013023a2f06c83b9b869a394861",
        "playwrightBaseAmd64Digest": "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9",
    }
    execution_error: dict[str, Any] | None = None
    interrupt_state = InterruptState()
    previous_handlers = {
        signum: signal.signal(signum, interrupt_state.handle)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        harness.verify_clean_source()
        image_ids = harness.build() if not args.skip_build else {
            "sidecar": harness.image_digest(SIDECAR_TAG),
            "agent": harness.image_digest(AGENT_TAG),
            "legacy": harness.image_digest(LEGACY_TAG),
        }
        if args.skip_build:
            expected_image_ids = {
                "sidecar": args.expected_sidecar_id,
                "agent": args.expected_agent_id,
                "legacy": args.expected_legacy_id,
            }
            if not all(
                isinstance(image_id, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                for image_id in expected_image_ids.values()
            ):
                raise HarnessError(
                    "--skip-build requires all three --expected-*-id values as sha256 digests"
                )
            if image_ids != expected_image_ids:
                raise HarnessError(
                    "--skip-build image identity mismatch: "
                    f"expected {expected_image_ids}, got {image_ids}"
                )
            expected_revisions = {
                "sidecar": args.expected_sidecar_revision,
                "agent": args.expected_agent_revision,
                "legacy": args.expected_legacy_revision,
            }
            if not all(
                isinstance(revision, str) and REVISION_RE.fullmatch(revision)
                for revision in expected_revisions.values()
            ):
                raise HarnessError(
                    "--skip-build requires all three --expected-*-revision values as 40-hex revisions"
                )
        else:
            expected_revisions = {
                "sidecar": harness.source_revision,
                "agent": harness.source_revision,
                "legacy": harness.source_revision,
            }
        images = {name: harness.image_metadata(tag) for name, tag in {
            "sidecar": SIDECAR_TAG,
            "agent": AGENT_TAG,
            "legacy": LEGACY_TAG,
        }.items()}
        validate_image_revisions(images, expected_revisions)
        evidence["p0"] = {
            "imageIds": image_ids,
            "expectedRevisions": expected_revisions,
            "images": images,
        }

        suite_request = render_request("suite", {
            "viewer": {"modelKey": "inspection-step", "inspectionControl": "toggle-projection"},
            "residual": residual_payload(),
        })
        suite = harness.start_job("suite")
        inspect = json.loads(harness.run("inspect", suite["containerId"]).stdout)[0]
        network = json.loads(harness.run("network", "inspect", suite["networkId"]).stdout)[0]
        suite_result = harness.run_client(suite, suite_request)
        suite_terminal = harness.stop_job(suite)
        evidence["p1"] = {
            "readonlyRootfs": inspect["HostConfig"]["ReadonlyRootfs"],
            "mounts": inspect["Mounts"],
            "tmpfs": inspect["HostConfig"]["Tmpfs"],
            "shmSize": inspect["HostConfig"]["ShmSize"],
            "memory": inspect["HostConfig"]["Memory"],
            "memorySwap": inspect["HostConfig"]["MemorySwap"],
            "nanoCpus": inspect["HostConfig"]["NanoCpus"],
            "pidsLimit": inspect["HostConfig"]["PidsLimit"],
            "capDrop": inspect["HostConfig"]["CapDrop"],
            "securityOpt": inspect["HostConfig"]["SecurityOpt"],
            "portBindings": inspect["HostConfig"]["PortBindings"],
            "user": inspect["Config"]["User"],
            "networkInternal": network["Internal"],
            "probe": suite_result["result"]["probe"],
            "terminal": suite_terminal,
        }
        public_request = render_request("residual", residual_payload())
        baseline_public = harness.run_public_parity(
            f"{PREFIX}-public-baseline", public_request, mode="baseline"
        )
        parity_job = harness.start_job("public-parity")
        remote_public = harness.run_public_parity(
            f"{PREFIX}-public-remote", public_request, mode="remote", job=parity_job
        )
        parity_terminal = harness.stop_job(parity_job)
        evidence["p2"] = {
            "nodeSuite": suite_result,
            "baselinePublic": baseline_public,
            "remotePublic": remote_public,
            "publicParity": {
                "pngBytesEqual": baseline_public["pngBytes"] == remote_public["pngBytes"],
                "pngSha256Equal": baseline_public["pngSha256"] == remote_public["pngSha256"],
                "imageModeEqual": baseline_public["imageMode"] == remote_public["imageMode"] == "RGB",
                "imageSizeEqual": baseline_public["imageSize"] == remote_public["imageSize"],
                "profileSha256Equal": baseline_public["projection"]["profileSha256"] == remote_public["projection"]["profileSha256"],
                "viewsEqual": baseline_public["projection"]["views"] == remote_public["projection"]["views"],
                "variantEqual": baseline_public["projection"]["variant"] == remote_public["projection"]["variant"],
                "evidenceSha256Equal": baseline_public["evidenceSha256"] == remote_public["evidenceSha256"],
            },
            "remoteTerminal": parity_terminal,
        }

        job_a = harness.start_job("job-a", cpus="0.75", memory="1g")
        job_b = harness.start_job("job-b", cpus="0.75", memory="1g")
        client_a = harness.run_client(job_a, hold_request("job-a-marker", "job-b-marker", two_triangles=False), detached=True)
        client_b = harness.run_client(job_b, hold_request("job-b-marker", "job-a-marker", two_triangles=True), detached=True)
        if client_a.process.stdout is None or client_b.process.stdout is None:
            raise HarnessError("detached client stdout was not captured")
        ready_a = json.loads(client_a.process.stdout.readline().strip())
        ready_b = json.loads(client_b.process.stdout.readline().strip())
        if ready_a.get("event") != "hold-ready" or ready_b.get("event") != "hold-ready":
            raise HarnessError(f"clients did not enter hold: {ready_a!r}, {ready_b!r}")
        top_a = harness.run("top", client_a.container_id, "-eo", "pid,ppid,user,comm,args").stdout
        top_b = harness.run("top", client_b.container_id, "-eo", "pid,ppid,user,comm,args").stdout
        terminal_a = harness.stop_job(job_a)
        stdout_a, stderr_a = harness.finish_detached(client_a)
        stdout_b, stderr_b = harness.finish_detached(client_b)
        state_b_before_stop = json.loads(harness.run("inspect", job_b["containerId"], "--format", "{{json .State}}").stdout)
        terminal_b = harness.stop_job(job_b)
        evidence["p3"] = {
            "agentTopA": top_a,
            "agentTopB": top_b,
            "holdReadyA": ready_a,
            "holdReadyB": ready_b,
            "clientA": {"exitCode": client_a.process.returncode, "stdout": stdout_a, "stderr": stderr_a},
            "clientB": {"exitCode": client_b.process.returncode, "stdout": stdout_b, "stderr": stderr_b},
            "jobBRunningAfterJobACancel": state_b_before_stop["Running"],
            "terminalA": terminal_a,
            "terminalB": terminal_b,
        }
    except Exception as exc:
        execution_error = {"type": type(exc).__name__, "message": str(exc)}
        evidence["executionError"] = execution_error
    finally:
        interrupt_state.cleanup_started = True
        try:
            try:
                evidence["cleanup"] = harness.cleanup_all()
            except Exception as exc:
                evidence["cleanup"] = {
                    "resources": [vars(item) for item in [*harness.ledger.containers, *harness.ledger.networks]],
                    "failures": [{
                        "kind": "cleanup",
                        "id": "central",
                        "argv": [],
                        "exitCode": None,
                        "stderr": str(exc),
                    }],
                    "firstFailure": {"kind": "cleanup", "id": "central", "stderr": str(exc)},
                    "absenceProofs": [],
                    "pendingNames": {
                        "containers": sorted(harness.pending_container_names),
                        "networks": sorted(harness.pending_network_names),
                    },
                }
            residue: dict[str, list[str]] = {}
            residue_errors: dict[str, str] = {}
            for kind, command in {
                "containers": ("ps", "-a", "--filter", f"name={PREFIX}", "--format", "{{.Names}}"),
                "networks": ("network", "ls", "--filter", f"name={PREFIX}", "--format", "{{.Name}}"),
            }.items():
                try:
                    residue[kind] = harness.run(*command).stdout.splitlines()
                except Exception as exc:
                    residue[kind] = ["<inspection-error>"]
                    residue_errors[kind] = str(exc)
            evidence["residue"] = residue
            if residue_errors:
                evidence["residueErrors"] = residue_errors
            if interrupt_state.signal_name is not None:
                evidence["interruptionSignal"] = interrupt_state.signal_name
            evidence["predicates"] = predicate_matrix(evidence)
            evidence["predicates"]["execution.completed_without_error"] = (
                execution_error is None and interrupt_state.signal_name is None
            )
            evidence["verdict"] = "ADOPT" if all(evidence["predicates"].values()) else "REJECT"
            evidence["commands"] = harness.commands
            evidence["finishedAtUnix"] = time.time()
            output = args.evidence_dir / "evidence.json"
            output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf8")
            print(output)
        finally:
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)
    return 0 if evidence.get("verdict") == "ADOPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
