#!/usr/bin/env python3
"""THROWAWAY fixed Agent entrypoint for the SAR-003 public-seam decision."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


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
MANIFEST_KEYS = {
    "schema", "jobId", "ownerNonce", "agentImageId", "sourceDigest",
    "inputDigest", "runtimeManifestDigest", "brokerAuthorityDigest", "workload",
}
FORBIDDEN_NAMES = ("chromium", "chrome", "playwright", "docker.sock", "podman.sock")


class GateError(RuntimeError):
    pass


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise GateError("snapshot tree is not closed")
        mode = path.lstat().st_mode & 0o7777
        digest.update(("d" if path.is_dir() else "f").encode() + b"\0" + relative.encode() + b"\0" + oct(mode).encode() + b"\0")
        if path.is_file():
            digest.update(str(path.stat().st_size).encode() + b"\0" + path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _mount_options(target: Path) -> set[str]:
    matches = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        left, _separator, _right = line.partition(" - ")
        fields = left.split()
        if len(fields) >= 6 and fields[4].replace("\\040", " ") == str(target):
            matches.append(set(fields[5].split(",")))
    if len(matches) != 1:
        raise GateError(f"exact mount missing or duplicated: {target}")
    return matches[0]


def _assert_readonly(target: Path) -> None:
    if "ro" not in _mount_options(target):
        raise GateError(f"mount is writable: {target}")


def _assert_writable(target: Path) -> None:
    marker = target / f".boundary-write-{os.getpid()}"
    marker.write_bytes(b"")
    marker.unlink()


def _deny_browser_and_docker() -> None:
    for variable in os.environ:
        if variable.startswith(("DOCKER_", "CONTAINER_HOST", "PLAYWRIGHT_")):
            raise GateError("forbidden ambient authority variable")
    for path in (Path("/var/run/docker.sock"), Path("/run/docker.sock"), Path("/run/podman/podman.sock")):
        if path.exists():
            raise GateError("container runtime socket exposed")
    for root in (Path("/usr"), Path("/opt"), SOURCE, INPUT, *WRITABLE):
        for path in root.rglob("*"):
            lowered = path.name.lower()
            if any(marker in lowered for marker in FORBIDDEN_NAMES):
                raise GateError("browser or container authority material visible")
    routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    if any(line.split()[1] == "00000000" for line in routes if len(line.split()) > 1):
        raise GateError("external network route visible")


def _publish(payload: dict[str, object], expected_ack: str) -> None:
    """Use Docker attach as the exact-ID-bound outer authority channel."""
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    if sys.stdin.readline() != expected_ack + "\n":
        raise GateError("outer authority withheld acknowledgement")


def _probe_broker(manifest: dict[str, object]) -> None:
    request = {
        "schema": "meshshot.agent-boundary.broker-probe/1",
        "jobId": manifest["jobId"],
        "ownerNonce": manifest["ownerNonce"],
        "brokerAuthorityDigest": manifest["brokerAuthorityDigest"],
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
    expected = {"schema": "meshshot.agent-boundary.broker-proof/1", **{key: request[key] for key in ("jobId", "ownerNonce", "brokerAuthorityDigest")}}
    try:
        observed = json.loads(response)
    except json.JSONDecodeError as exc:
        raise GateError("Broker proof is malformed") from exc
    if observed != expected:
        raise GateError("Broker proof does not bind this job authority")


def main() -> int:
    manifest_path = CONTROL / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != MANIFEST_KEYS or manifest["schema"] != "meshshot.agent-boundary/1":
        raise GateError("invalid manifest")
    if not isinstance(manifest["jobId"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", manifest["jobId"]):
        raise GateError("invalid job identity")
    if not isinstance(manifest["ownerNonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", manifest["ownerNonce"]):
        raise GateError("invalid owner nonce")
    for field in ("agentImageId", "runtimeManifestDigest", "sourceDigest", "inputDigest", "brokerAuthorityDigest"):
        if not isinstance(manifest[field], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest[field]):
            raise GateError("invalid digest identity")
    for target in (Path("/"), SOURCE, INPUT, CONTROL, BROKER):
        _assert_readonly(target)
    for target in WRITABLE:
        _assert_writable(target)
    if _tree_digest(SOURCE) != manifest["sourceDigest"] or _tree_digest(INPUT) != manifest["inputDigest"]:
        raise GateError("snapshot identity mismatch")
    runtime_manifest_digest = "sha256:" + hashlib.sha256(RUNTIME_MANIFEST.read_bytes()).hexdigest()
    if runtime_manifest_digest != manifest["runtimeManifestDigest"]:
        raise GateError("runtime manifest identity mismatch")
    authority = BROKER / "authority.json"
    authority_digest = "sha256:" + hashlib.sha256(authority.read_bytes()).hexdigest()
    if authority_digest != manifest["brokerAuthorityDigest"]:
        raise GateError("Broker authority identity mismatch")
    _deny_browser_and_docker()
    _probe_broker(manifest)
    identity = {key: manifest[key] for key in ("jobId", "ownerNonce", "agentImageId", "runtimeManifestDigest", "sourceDigest", "inputDigest", "brokerAuthorityDigest")}
    _publish({"schema": "meshshot.agent-boundary.preflight/1", **identity}, "RELEASE")

    workload = manifest["workload"]
    if not isinstance(workload, list) or not workload or not all(isinstance(item, str) and item for item in workload):
        raise GateError("workload argv must be one fixed nonempty string array")
    with (WRITABLE[-1] / "agent.stdout").open("wb") as stdout, (WRITABLE[-1] / "agent.stderr").open("wb") as stderr:
        completed = subprocess.run(workload, cwd="/run/agent-job/work", env={
            "HOME": str(WRITABLE[0]), "CODEX_HOME": str(WRITABLE[0] / ".codex"),
            "XDG_CACHE_HOME": str(WRITABLE[1]), "TMPDIR": str(WRITABLE[2]),
            "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "TZ": "UTC", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1",
        }, stdout=stdout, stderr=stderr, check=False)
    output_digest = _tree_digest(WRITABLE[-1])
    _publish({
        "schema": "meshshot.agent-boundary.terminal/1", **identity,
        "workloadStatus": completed.returncode, "outputDigest": output_digest,
    }, "ACK")
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"agent-boundary entrypoint failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(125)
