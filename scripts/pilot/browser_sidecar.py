#!/usr/bin/env python3
"""Own one exact Browser Sidecar and its registered-program broker per pilot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


IMAGE_ID = "sha256:22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1"
IMAGE_SOURCE_REVISION = "1abe4c97929906b5c0b28b0f3f38857bd923952f"
PROGRAMS = {
    "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
    "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
}
AUTHORITY_SCHEMA = "meshshot.browser-authority/1"
BROKER_SCHEMA = "meshshot.browser-sidecar.broker/1"
RECEIPT_SCHEMA = "meshshot.browser-sidecar.job-receipt/1"
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
RESOURCE_ID = re.compile(r"[0-9a-f]{64}\Z")
LOOPBACK_PORT = re.compile(r"127\.0\.0\.1:([1-9][0-9]{0,4})\Z")
IMAGE_PROJECTIONS = (
    ("id", "{{.Id}}", IMAGE_ID),
    ("os", "{{.Os}}", "linux"),
    ("architecture", "{{.Architecture}}", "amd64"),
    (
        "revision",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        IMAGE_SOURCE_REVISION,
    ),
)


class BrowserSidecarError(RuntimeError):
    """One closed formal-pilot Browser Sidecar lifecycle failure."""

    def __init__(self, message: str, *, check: str) -> None:
        super().__init__(message)
        self.check = check


def _strict_json(raw: str, label: str) -> Any:
    """Decode duplicate-free JSON from one fixed Docker/broker projection."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BrowserSidecarError(
                    f"{label} contains duplicate keys",
                    check=f"{label}-format",
                )
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise BrowserSidecarError(
            f"{label} is not JSON",
            check=f"{label}-format",
        ) from exc


def _write_json_atomic(path: Path, payload: object) -> None:
    """Atomically publish one canonical JSON receipt or authority file."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class BrowserSidecarJob:
    """Public adapter owning one exact OCI Sidecar for one pilot job."""

    def __init__(
        self,
        exp_dir: Path,
        sandbox_exp_dir: Path,
        *,
        job_id: str,
    ) -> None:
        """Bind immutable identities before any Docker resource is created."""

        if JOB_ID.fullmatch(job_id) is None:
            raise BrowserSidecarError("job identity is invalid", check="job-id")
        self.exp_dir = exp_dir.resolve()
        self.sandbox_exp_dir = sandbox_exp_dir
        self.job_id = job_id
        self.run_dir = self.exp_dir / "run"
        self.authority_path = self.run_dir / "browser-authority.json"
        self.socket_path = self.run_dir / "browser-sidecar.sock"
        self.receipt_path = self.run_dir / "browser-sidecar-receipt.json"
        self.owner_nonce = secrets.token_hex(16)
        self.prefix = f"ttc-bs-{self.owner_nonce[:12]}"
        self.network_name = f"{self.prefix}-net"
        self.container_name = f"{self.prefix}-sidecar"
        self.label = f"io.text-to-cad.browser-sidecar-job={self.job_id}"
        self.owner_label = (
            f"io.text-to-cad.browser-sidecar-owner={self.owner_nonce}"
        )
        self.docker: str | None = None
        self.network_id: str | None = None
        self.container_id: str | None = None
        self.broker: subprocess.Popen[bytes] | None = None
        self.readiness: Mapping[str, Any] | None = None
        self.request_count = 0
        self.first_error: str | None = None
        self.cleanup_errors: list[str] = []
        self._closed = False

    def _docker(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        """Run one fixed Docker command with bounded output and timeout."""

        if self.docker is None:
            raise BrowserSidecarError(
                "Docker was not resolved",
                check="docker-access",
            )
        try:
            completed = subprocess.run(
                [self.docker, *arguments],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrowserSidecarError(
                "fixed Docker operation failed",
                check=f"docker-{arguments[0]}-access",
            ) from exc
        if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
            raise BrowserSidecarError(
                "fixed Docker output exceeded its bound",
                check=f"docker-{arguments[0]}-format",
            )
        if check and completed.returncode:
            raise BrowserSidecarError(
                "fixed Docker operation returned nonzero",
                check=f"docker-{arguments[0]}-status",
            )
        return completed

    def _require_absent_name(self, kind: str, name: str) -> None:
        """Reject a foreign predictable name without adopting or deleting it."""

        existing = self._docker(kind, "inspect", name, check=False)
        if existing.returncode == 0:
            raise BrowserSidecarError(
                f"foreign {kind} name already exists",
                check=f"foreign-{kind}-name",
            )
        if existing.returncode not in (1,):
            raise BrowserSidecarError(
                f"cannot prove {kind} name absence",
                check=f"{kind}-name-absence",
            )

    def _inspect_image(self) -> None:
        """Require the exact pre-provisioned linux/amd64 reviewed image."""

        address = IMAGE_ID.removeprefix("sha256:")
        for field, projection, expected in IMAGE_PROJECTIONS:
            completed = self._docker(
                "inspect",
                "--type=image",
                "--format",
                projection,
                address,
            )
            lines = completed.stdout.splitlines()
            if lines != [expected]:
                raise BrowserSidecarError(
                    f"Sidecar image {field} mismatch",
                    check=f"image-{field}",
                )

    def _wait_sidecar_ready(self) -> Mapping[str, Any]:
        """Wait for one exact readiness record without replacing the Sidecar."""

        if self.container_id is None:
            raise BrowserSidecarError("Sidecar was not created", check="readiness")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            logs = self._docker("logs", "--tail", "50", self.container_id)
            for line in logs.stdout.splitlines():
                if not line.startswith("{"):
                    continue
                record = _strict_json(line, "readiness")
                if (
                    isinstance(record, dict)
                    and set(record) == {"event", "jobId", "endpointPath", "programs"}
                    and record.get("event") == "ready"
                    and record.get("jobId") == self.job_id
                    and record.get("programs") == PROGRAMS
                    and isinstance(record.get("endpointPath"), str)
                    and str(record["endpointPath"]).startswith("/")
                ):
                    return record
            state = self._docker(
                "container",
                "inspect",
                self.container_id,
                "--format",
                "{{json .State}}",
                check=False,
            )
            if state.returncode:
                raise BrowserSidecarError(
                    "Sidecar disappeared before readiness",
                    check="readiness-exit",
                )
            payload = _strict_json(state.stdout, "sidecar-state")
            if not isinstance(payload, dict) or payload.get("Running") is not True:
                raise BrowserSidecarError(
                    "Sidecar stopped before readiness",
                    check="readiness-exit",
                )
            time.sleep(0.1)
        raise BrowserSidecarError(
            "Sidecar readiness deadline exceeded",
            check="readiness-timeout",
        )

    def _published_port(self, container_port: int) -> int:
        """Resolve one Docker-assigned loopback port with exact framing."""

        if self.container_id is None:
            raise BrowserSidecarError("Sidecar was not created", check="port")
        completed = self._docker("port", self.container_id, f"{container_port}/tcp")
        lines = completed.stdout.splitlines()
        match = LOOPBACK_PORT.fullmatch(lines[0]) if len(lines) == 1 else None
        if match is None or int(match.group(1)) > 65535:
            raise BrowserSidecarError(
                "Sidecar loopback port projection is invalid",
                check="port-format",
            )
        return int(match.group(1))

    def _start_broker(self, endpoint_path: str, ports: Mapping[int, int]) -> None:
        """Start the only host broker and require its bound identity record."""

        argv = [
            sys.executable,
            str(Path(__file__)),
            "broker",
            "--job-id",
            self.job_id,
            "--socket",
            str(self.socket_path),
            "--browser-endpoint",
            f"ws://127.0.0.1:{ports[3000]}{endpoint_path}",
            "--viewer-origin",
            f"http://127.0.0.1:{ports[4173]}",
            "--residual-origin",
            f"http://127.0.0.1:{ports[4174]}",
        ]
        try:
            self.broker = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise BrowserSidecarError(
                "registered-program broker did not start",
                check="broker-start",
            ) from exc
        if self.broker.stdout is None:
            raise BrowserSidecarError(
                "registered-program broker has no readiness channel",
                check="broker-readiness",
            )
        ready = self.broker.stdout.readline(16 * 1024)
        if not ready.endswith(b"\n"):
            raise BrowserSidecarError(
                "registered-program broker readiness is incomplete",
                check="broker-readiness",
            )
        try:
            record = _strict_json(ready.decode("ascii"), "broker-readiness")
        except UnicodeDecodeError as exc:
            raise BrowserSidecarError(
                "registered-program broker readiness is invalid",
                check="broker-readiness",
            ) from exc
        if record != {
            "event": "ready",
            "schema": BROKER_SCHEMA,
            "jobId": self.job_id,
            "imageId": IMAGE_ID,
            "programs": PROGRAMS,
        }:
            raise BrowserSidecarError(
                "registered-program broker identity mismatch",
                check="broker-readiness",
            )

    def start(self) -> Path:
        """Start one exact Sidecar and publish its bounded sandbox authority."""

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.authority_path.unlink(missing_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self.docker = shutil.which("docker")
        if self.docker is None:
            raise BrowserSidecarError(
                "Docker is required for the formal Browser Sidecar",
                check="docker-access",
            )
        try:
            self._inspect_image()
            self._require_absent_name("network", self.network_name)
            self._require_absent_name("container", self.container_name)
            created = self._docker(
                "network",
                "create",
                "--internal",
                "--label",
                self.label,
                "--label",
                self.owner_label,
                self.network_name,
            )
            network_id = created.stdout.strip()
            if RESOURCE_ID.fullmatch(network_id) is None:
                raise BrowserSidecarError(
                    "created network identity is invalid",
                    check="network-id",
                )
            self.network_id = network_id
            started = self._docker(
                "run",
                "-d",
                "--name",
                self.container_name,
                "--label",
                self.label,
                "--label",
                self.owner_label,
                "--network",
                self.network_name,
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--read-only",
                "--init",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "256",
                "--memory",
                "1536m",
                "--memory-swap",
                "1536m",
                "--cpus",
                "1.5",
                "--shm-size",
                "256m",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                "--tmpfs",
                "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
                "-p",
                "127.0.0.1::3000",
                "-p",
                "127.0.0.1::4173",
                "-p",
                "127.0.0.1::4174",
                "-e",
                f"BROWSER_SIDECAR_JOB_ID={self.job_id}",
                IMAGE_ID,
            )
            container_id = started.stdout.strip()
            if RESOURCE_ID.fullmatch(container_id) is None:
                raise BrowserSidecarError(
                    "created Sidecar identity is invalid",
                    check="container-id",
                )
            self.container_id = container_id
            self.readiness = self._wait_sidecar_ready()
            ports = {
                port: self._published_port(port)
                for port in (3000, 4173, 4174)
            }
            self._start_broker(str(self.readiness["endpointPath"]), ports)
            authority = {
                "schema": AUTHORITY_SCHEMA,
                "jobId": self.job_id,
                "imageId": IMAGE_ID,
                "socketPath": str(
                    self.sandbox_exp_dir / "run" / "browser-sidecar.sock"
                ),
                "programs": PROGRAMS,
            }
            _write_json_atomic(self.authority_path, authority)
            return self.authority_path
        except BaseException as exc:
            self.first_error = (
                exc.check if isinstance(exc, BrowserSidecarError) else "start-unexpected"
            )
            self.close(workload_status=None)
            raise

    def poll_failed(self) -> bool:
        """Return whether the exact Sidecar or broker exited during workload."""

        if self.broker is not None and self.broker.poll() is not None:
            return True
        if self.container_id is None:
            return True
        state = self._docker(
            "container",
            "inspect",
            self.container_id,
            "--format",
            "{{json .State}}",
            check=False,
        )
        if state.returncode:
            return True
        try:
            payload = _strict_json(state.stdout, "sidecar-state")
        except BrowserSidecarError:
            return True
        return not isinstance(payload, dict) or payload.get("Running") is not True

    def _prove_absence(self) -> Mapping[str, object]:
        """Prove no resource with both exact job and owner labels remains."""

        errors: list[str] = []
        retained: dict[str, list[str]] = {"containers": [], "networks": []}
        for kind, key in (("container", "containers"), ("network", "networks")):
            try:
                completed = self._docker(
                    kind,
                    "ls",
                    "-a" if kind == "container" else "--no-trunc",
                    "--filter",
                    f"label={self.label}",
                    "--filter",
                    f"label={self.owner_label}",
                    "--format",
                    "{{.ID}}",
                    check=False,
                )
            except BrowserSidecarError:
                errors.append(f"{kind}-absence")
                continue
            if completed.returncode:
                errors.append(f"{kind}-absence")
            retained[key] = completed.stdout.split()
        return {
            **retained,
            "errors": errors,
            "proved": not errors and not retained["containers"] and not retained["networks"],
        }

    def close(self, *, workload_status: int | None) -> Mapping[str, object]:
        """Perform bounded reverse-order cleanup and publish terminal evidence."""

        if self._closed:
            try:
                return _strict_json(
                    self.receipt_path.read_text(encoding="utf-8"),
                    "job-receipt",
                )
            except OSError as exc:
                raise BrowserSidecarError(
                    "terminal Browser Sidecar receipt is unavailable",
                    check="receipt",
                ) from exc
        self._closed = True
        broker_status: int | None = None
        if self.broker is not None:
            try:
                if self.broker.poll() is None:
                    self.broker.terminate()
                broker_status = self.broker.wait(timeout=10)
                if broker_status != 0:
                    self.cleanup_errors.append("broker-terminal")
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.broker.kill()
                    self.broker.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                self.cleanup_errors.append("broker-terminal")
        terminal: Mapping[str, Any] | None = None
        if self.container_id is not None:
            stopped = self._docker(
                "stop",
                "--time",
                "15",
                self.container_id,
                check=False,
            )
            if stopped.returncode:
                self.cleanup_errors.append("sidecar-stop")
            inspected = self._docker(
                "container",
                "inspect",
                self.container_id,
                "--format",
                "{{json .State}}",
                check=False,
            )
            if inspected.returncode:
                self.cleanup_errors.append("sidecar-terminal")
            else:
                try:
                    payload = _strict_json(inspected.stdout, "sidecar-terminal")
                    terminal = payload if isinstance(payload, dict) else None
                except BrowserSidecarError:
                    self.cleanup_errors.append("sidecar-terminal")
            removed = self._docker(
                "rm",
                "-f",
                self.container_id,
                check=False,
            )
            if removed.returncode:
                self.cleanup_errors.append("container-remove")
        if self.network_id is not None:
            removed = self._docker(
                "network",
                "rm",
                self.network_id,
                check=False,
            )
            if removed.returncode:
                self.cleanup_errors.append("network-remove")
        absence = (
            self._prove_absence()
            if self.docker is not None
            else {
                "containers": [],
                "networks": [],
                "errors": ["docker-absence"],
                "proved": False,
            }
        )
        if absence.get("proved") is not True:
            self.cleanup_errors.append("retained-resource")
        self.authority_path.unlink(missing_ok=True)
        self.socket_path.unlink(missing_ok=True)
        succeeded = (
            self.first_error is None
            and workload_status == 0
            and broker_status == 0
            and isinstance(terminal, dict)
            and terminal.get("ExitCode") == 0
            and not self.cleanup_errors
            and absence.get("proved") is True
        )
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "succeeded" if succeeded else "failed",
            "jobId": self.job_id,
            "ownerNonce": self.owner_nonce,
            "imageId": IMAGE_ID,
            "imageSourceRevision": IMAGE_SOURCE_REVISION,
            "programs": PROGRAMS,
            "readiness": self.readiness,
            "workloadStatus": workload_status,
            "brokerStatus": broker_status,
            "terminal": terminal,
            "absenceProof": absence,
            "cleanupErrors": list(dict.fromkeys(self.cleanup_errors)),
            "errorCheck": self.first_error,
            "retryAllowed": False,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.receipt_path, receipt)
        return receipt


def run_broker(args: argparse.Namespace) -> int:
    """Run the registered-program broker (implemented by the next TDD slice)."""

    del args
    return 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the fixed outer-only broker command."""

    parser = argparse.ArgumentParser(description="Browser Sidecar lifecycle helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    broker = subparsers.add_parser("broker")
    broker.add_argument("--job-id", required=True)
    broker.add_argument("--socket", type=Path, required=True)
    broker.add_argument("--browser-endpoint", required=True)
    broker.add_argument("--viewer-origin", required=True)
    broker.add_argument("--residual-origin", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the fixed registered-program broker action."""

    args = parse_args(argv)
    if args.action == "broker":
        return run_broker(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
