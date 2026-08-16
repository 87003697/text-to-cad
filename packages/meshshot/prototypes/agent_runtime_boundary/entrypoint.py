#!/usr/bin/env python3
"""THROWAWAY fixed Agent entrypoint for the SAR-003 public seam."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from typing import Mapping

import browser_surface
from contract import (
    ContractError, Digest, ExecutionIdentity, IDENTITY_KEYS,
    canonical_tree_digest, require_exact_record, workload_digest,
)
from process_group import run_workload_group


CONTROL = Path("/run/agent-boundary")
SOURCE = Path("/run/agent-job/source")
INPUT = Path("/run/agent-job/input")
WRITABLE = (
    Path("/run/agent-job/home"), Path("/run/agent-job/cache"),
    Path("/run/agent-job/tmp"), Path("/run/agent-job/work"),
    Path("/run/agent-job/output"),
)
BROKER = Path("/run/meshshot-browser")
RUNTIME_MANIFEST = Path("/opt/text-to-cad/runtime-manifest.json")
MANIFEST_KEYS = {"schema", "workload", *IDENTITY_KEYS}


class GateError(RuntimeError):
    pass


def _mount_options(target: Path) -> set[str]:
    matches = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.partition(" - ")[0].split()
        if len(fields) >= 6 and fields[4].replace("\\040", " ") == str(target):
            matches.append(set(fields[5].split(",")))
    if len(matches) != 1:
        raise GateError("exact mount missing or duplicated")
    return matches[0]


def _assert_writable(target: Path) -> None:
    marker = target / f".boundary-write-{os.getpid()}"
    marker.write_bytes(b"")
    marker.unlink()


def _deny_browser_docker_and_route(scan_roots: tuple[Path, ...]) -> None:
    for variable in os.environ:
        if variable.startswith(("DOCKER_", "CONTAINER_HOST", "PLAYWRIGHT_")):
            raise GateError("forbidden ambient authority variable")
    for path in (Path("/var/run/docker.sock"), Path("/run/docker.sock"), Path("/run/podman/podman.sock")):
        if path.exists():
            raise GateError("container runtime socket exposed")
    mounts = [(root, root, True) for root in scan_roots]
    findings = browser_surface.discover_browser_roots(
        mounts, permitted_symlink_roots=scan_roots,
    )
    if findings:
        raise GateError("formal browser-surface scanner found denied material")
    routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    if any(line.split()[1] == "00000000" for line in routes if len(line.split()) > 1):
        raise GateError("external network route visible")


def _publish(payload: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _read_record(max_bytes: int = 16384) -> Mapping[str, object]:
    line = sys.stdin.buffer.readline(max_bytes + 1)
    if not line.endswith(b"\n") or len(line) > max_bytes:
        raise GateError("outer protocol record is unavailable")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise GateError("outer protocol record is invalid")
    return value


def _probe_broker(identity: ExecutionIdentity, challenge: str) -> Mapping[str, object]:
    request = {
        "schema": "meshshot.agent-boundary.broker-challenge/1",
        **identity.as_json(), "challenge": challenge,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(BROKER / "browser.sock"))
        client.sendall(json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        response = b""
        while not response.endswith(b"\n") and len(response) <= 4096:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
    value = json.loads(response)
    if not isinstance(value, dict):
        raise GateError("Broker proof is malformed")
    require_exact_record(
        value, "meshshot.agent-boundary.broker-proof/1", identity,
        ("challenge", "brokerMac"),
    )
    if value.get("challenge") != challenge:
        raise GateError("Broker proof challenge is wrong")
    return value


def main() -> int:
    manifest = json.loads((CONTROL / "manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or manifest.get("schema") != "meshshot.agent-boundary/3"
    ):
        raise GateError("invalid manifest")
    identity = ExecutionIdentity.from_mapping(manifest)
    workload_value = manifest["workload"]
    if not isinstance(workload_value, list):
        raise GateError("workload argv is invalid")
    workload = tuple(workload_value)
    if workload_digest(workload) != identity.workload_digest:
        raise GateError("workload identity mismatch")
    for target in (Path("/"), SOURCE, INPUT, CONTROL, BROKER):
        if "ro" not in _mount_options(target):
            raise GateError("required read-only mount is writable")
    for target in WRITABLE:
        _assert_writable(target)
    if (
        canonical_tree_digest(SOURCE) != identity.source_digest
        or canonical_tree_digest(INPUT) != identity.input_digest
    ):
        raise GateError("snapshot identity mismatch")
    runtime_manifest_bytes = RUNTIME_MANIFEST.read_bytes()
    if Digest("sha256:" + hashlib.sha256(runtime_manifest_bytes).hexdigest()) != identity.runtime_manifest_digest:
        raise GateError("runtime manifest identity mismatch")
    authority = BROKER / "authority.json"
    if Digest("sha256:" + hashlib.sha256(authority.read_bytes()).hexdigest()) != identity.broker_authority_digest:
        raise GateError("Broker authority identity mismatch")
    runtime_manifest = json.loads(runtime_manifest_bytes)
    if not isinstance(runtime_manifest, dict):
        raise GateError("runtime manifest is invalid")
    roots = runtime_manifest.get("browserScanRoots")
    if (
        not isinstance(roots, list)
        or not roots
        or not all(isinstance(item, str) and item.startswith("/") for item in roots)
    ):
        raise GateError("browser scan roots are invalid")
    _deny_browser_docker_and_route(tuple(Path(item) for item in roots))

    _publish({"schema": "meshshot.agent-boundary.ready/1", **identity.as_json()})
    challenge_record = _read_record()
    require_exact_record(
        challenge_record, "meshshot.agent-boundary.challenge/1", identity,
        ("challenge",),
    )
    challenge = challenge_record.get("challenge")
    if not isinstance(challenge, str):
        raise GateError("challenge is invalid")
    broker_proof = _probe_broker(identity, challenge)
    _publish({
        "schema": "meshshot.agent-boundary.preflight/2", **identity.as_json(),
        "challenge": challenge, "brokerMac": broker_proof["brokerMac"],
    })
    release = _read_record()
    require_exact_record(release, "meshshot.agent-boundary.release/1", identity)

    with (WRITABLE[-1] / "agent.stdout").open("wb") as stdout, (WRITABLE[-1] / "agent.stderr").open("wb") as stderr:
        result = run_workload_group(
            workload, cwd=str(WRITABLE[-2]), stdout=stdout, stderr=stderr,
            env={
                "HOME": str(WRITABLE[0]), "CODEX_HOME": str(WRITABLE[0] / ".codex"),
                "XDG_CACHE_HOME": str(WRITABLE[1]), "TMPDIR": str(WRITABLE[2]),
                "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "TZ": "UTC", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    if not result.group_absent:
        raise GateError("workload process group remains")
    _publish({
        "schema": "meshshot.agent-boundary.terminal/3", **identity.as_json(),
        "workloadStatus": result.returncode,
        "outputDigest": canonical_tree_digest(WRITABLE[-1]).value,
        "processGroupAbsent": result.group_absent,
        "descendantResidue": result.descendant_residue,
    })
    ack = _read_record()
    require_exact_record(ack, "meshshot.agent-boundary.ack/1", identity)
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, GateError, OSError, ValueError, json.JSONDecodeError):
        print("agent-boundary entrypoint failed", file=sys.stderr)
        raise SystemExit(125)
