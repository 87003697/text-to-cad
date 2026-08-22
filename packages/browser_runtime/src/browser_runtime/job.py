"""Outer-owned lifecycle for a single per-job browser runtime container.

Each job creates:
- one job-private Docker bridge network named
  ``<prefix>-net``,
- one container named ``<prefix>-runtime`` running Chromium + Playwright
  MCP, with its internal port published to a random host loopback slot,
- one host-side capability directory (``exp_dir/run/browser-runtime/...``)
  that stages the Codex MCP config file bound into the sandbox.

The Agent inside bwrap dials the MCP HTTP endpoint via
``127.0.0.1:<published-port>``; only this pilot's own bwrap can find that
port through the config file that bwrap read-mounts into the sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import (
    CAD_RENDER_PROGRAMS,
    RUNTIME_CAPABILITY_SCHEMA,
    SANDBOX_RUNTIME_CAPABILITY_NAME,
    load_image_lock,
)


_LOOPBACK_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))
_MAX_HEALTH_RESPONSE_BYTES = 4096
_MAX_AUDIT_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_RENDER_LEDGER_ENTRIES = 4096
_MAX_RENDER_REQUEST_BYTES = 160 * 1024 * 1024
_MAX_RENDER_RESPONSE_BYTES = 64 * 1024 * 1024
_EXACT_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class BrowserRuntimeError(RuntimeError):
    """Raised when container start, port discovery, or cleanup fails."""


DockerRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run_docker(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=True, capture_output=True, text=True)


@dataclass
class BrowserRuntimeJob:
    """Lifecycle handle for one per-job browser runtime container.

    Callers own ``capability_dir`` (a job-private directory) and are
    responsible for wiring the resulting ``mcp_url`` into the Codex config
    they mount into bwrap. This class does not touch bwrap directly.
    """

    owner_nonce: str
    capability_dir: Path
    image_ref: str | None = None
    image_lock_path: Path | None = None
    docker: DockerRunner = field(default=_run_docker)
    port_ready_timeout_s: float = 20.0
    port_ready_poll_s: float = 0.15
    cpu_limit: str = "1.5"
    memory_limit: str = "2500m"
    shm_size: str = "1g"
    pids_limit: int = 512
    container_port: int = 9223
    cad_render_container_port: int = 9224

    def __post_init__(self) -> None:
        if len(self.owner_nonce) < 12:
            raise ValueError("owner_nonce must be at least 12 chars")
        self.prefix = f"ttc-br-{self.owner_nonce[:12]}"
        self.network_name = f"{self.prefix}-net"
        self.container_name = f"{self.prefix}-runtime"
        self.capability_dir = Path(self.capability_dir)
        if self.image_ref is None:
            lock = load_image_lock(self.image_lock_path)
            image = lock["image"]
            self.image_ref = image.get("id")
        if (
            not isinstance(self.image_ref, str)
            or _EXACT_IMAGE_ID.fullmatch(self.image_ref) is None
        ):
            raise BrowserRuntimeError("browser runtime requires an exact image ID")
        self._started = False
        self._stop_completed = False
        self._render_ledger_required = False
        self._captured_ledger_bytes: bytes | None = None
        self._captured_ledger_count: int | None = None
        self._published_port: int | None = None
        self._cad_render_published_port: int | None = None
        self._cad_render_token = secrets.token_hex(32)

    # -- factory ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        exp_dir: Path,
        *,
        owner_nonce: str | None = None,
        image_ref: str | None = None,
        image_lock_path: Path | None = None,
        docker: DockerRunner | None = None,
    ) -> "BrowserRuntimeJob":
        """Allocate a per-job capability directory under EXP_DIR/run/."""

        nonce = owner_nonce or secrets.token_hex(16)
        capability_dir = Path(exp_dir) / "run" / "browser-runtime" / nonce[:16]
        kwargs: dict = {
            "owner_nonce": nonce,
            "capability_dir": capability_dir,
        }
        if image_ref is not None:
            kwargs["image_ref"] = image_ref
        if image_lock_path is not None:
            kwargs["image_lock_path"] = image_lock_path
        if docker is not None:
            kwargs["docker"] = docker
        return cls(**kwargs)

    # -- attributes -------------------------------------------------------

    @property
    def mcp_url(self) -> str:
        """Streamable HTTP MCP endpoint the sandbox uses to dial the container."""

        if self._published_port is None:
            raise BrowserRuntimeError("browser runtime is not started")
        return f"http://127.0.0.1:{self._published_port}/mcp"

    @property
    def published_port(self) -> int:
        if self._published_port is None:
            raise BrowserRuntimeError("browser runtime is not started")
        return self._published_port

    @property
    def cad_render_url(self) -> str:
        """Fixed residual-render endpoint exposed by this job's sidecar."""

        if self._cad_render_published_port is None:
            raise BrowserRuntimeError("CAD render runtime is not started")
        return (
            f"http://127.0.0.1:{self._cad_render_published_port}"
            "/cad/render/residual"
        )

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._stop_completed = False
        self._render_ledger_required = False
        self._captured_ledger_bytes = None
        self._captured_ledger_count = None
        self.capability_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._resolve_local_image_ref()
        self._docker_or_raise(
            ["docker", "network", "create", self.network_name],
            purpose="create per-job docker network",
        )
        try:
            self._docker_or_raise(
                self._build_run_argv(),
                purpose="start browser runtime container",
            )
            self._published_port = self._discover_published_port(self.container_port)
            self._cad_render_published_port = self._discover_published_port(
                self.cad_render_container_port
            )
            self._started = True
            self._wait_for_port()
            self._publish_runtime_capability()
        except BrowserRuntimeError:
            self._docker_ignore(
                ["docker", "rm", "--force", "--volumes", self.container_name]
            )
            self._docker_ignore(["docker", "network", "rm", self.network_name])
            self._started = False
            self._published_port = None
            self._cad_render_published_port = None
            raise

    def _resolve_local_image_ref(self) -> None:
        """Require the exact locked image; runtime tags are not authority."""

        try:
            self.docker(["docker", "image", "inspect", self.image_ref])
        except subprocess.CalledProcessError as exc:
            raise BrowserRuntimeError(
                f"exact browser runtime image is unavailable: {self.image_ref}"
            ) from exc
        except FileNotFoundError as exc:
            raise BrowserRuntimeError("docker executable not found") from exc

    def stop(self) -> None:
        if self._stop_completed:
            return
        if self._started:
            self._render_ledger_required = True
        ledger_required = self._render_ledger_required
        errors: list[str] = []
        if ledger_required and self._captured_ledger_bytes is None:
            try:
                (
                    self._captured_ledger_bytes,
                    self._captured_ledger_count,
                ) = self._capture_render_ledger()
            except BrowserRuntimeError as exc:
                errors.append(str(exc))

        ledger_bytes = self._captured_ledger_bytes
        ledger_count = self._captured_ledger_count

        self._docker_ignore(
            ["docker", "rm", "--force", "--volumes", self.container_name]
        )
        self._docker_ignore(["docker", "network", "rm", self.network_name])
        try:
            container_absent = self._docker_resource_absent(
                ["docker", "container", "inspect", self.container_name],
                missing_markers=("no such object", "no such container"),
            )
        except BrowserRuntimeError as exc:
            errors.append(str(exc))
            container_absent = False
        try:
            network_absent = self._docker_resource_absent(
                ["docker", "network", "inspect", self.network_name],
                missing_markers=("no such network",),
            )
        except BrowserRuntimeError as exc:
            errors.append(str(exc))
            network_absent = False
        if not container_absent:
            errors.append("browser runtime container remains after cleanup")
        if not network_absent:
            errors.append("browser runtime network remains after cleanup")

        cleanup_passed = not errors and (not ledger_required or ledger_bytes is not None)
        try:
            self._publish_cleanup_receipt(
                ledger_bytes=ledger_bytes,
                ledger_count=ledger_count,
                container_absent=container_absent,
                network_absent=network_absent,
                passed=cleanup_passed,
            )
            self._stop_completed = True
        except BrowserRuntimeError as exc:
            errors.append(str(exc))
        self._started = False
        self._published_port = None
        self._cad_render_published_port = None
        if errors:
            raise BrowserRuntimeError("; ".join(dict.fromkeys(errors)))

    def poll_failed(self) -> bool:
        """Return True if the container is no longer in a healthy running state."""

        if not self._started:
            return False
        try:
            result = self.docker(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Status}}",
                    self.container_name,
                ]
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return True
        return (result.stdout or "").strip() != "running"

    def preflight(self) -> None:
        """Render one fixed triangle before any paid Agent workload starts."""

        triangle = {
            "schema": "text-to-cad.packed-triangle-mesh/1",
            "vertexCount": 3,
            "faceCount": 1,
            "positionsF32LeBase64": base64.b64encode(
                struct.pack(
                    "<9f",
                    -0.35, -0.3, 0.0,
                    0.35, -0.3, 0.0,
                    0.0, 0.35, 0.0,
                )
            ).decode("ascii"),
            "indicesU32LeBase64": base64.b64encode(
                struct.pack("<3I", 0, 1, 2)
            ).decode("ascii"),
        }
        request_value = {
            "schema": "text-to-cad.cad-render-request/2",
            "jobId": self.owner_nonce,
            "program": "residual",
            "programDigest": CAD_RENDER_PROGRAMS["residual"],
            "payload": {
                "reference": triangle,
                "candidate": triangle,
                "variant": "step",
                "exteriorDirections": [],
                "options": {
                    "cameraPolicy": "profile-fixed",
                    "canonicalPostprocess": True,
                },
            },
        }
        encoded = json.dumps(
            request_value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        request = urllib_request.Request(
            self.cad_render_url,
            data=encoded,
            headers={
                "authorization": f"Bearer {self._cad_render_token}",
                "content-type": "application/json",
            },
            method="POST",
        )
        response_bytes: bytes | None = None
        last_error: BaseException | None = None
        # Docker's newly published loopback forwarder can accept a TCP probe
        # just before it is ready to carry the first HTTP request. Retrying the
        # fixed, side-effect-free triangle avoids mistaking that brief transport
        # warm-up for a broken render program.
        for attempt in range(3):
            try:
                with _LOOPBACK_OPENER.open(request, timeout=30.0) as response:
                    response_bytes = response.read(16 * 1024 * 1024 + 1)
                break
            except urllib_error.HTTPError as exc:
                raise BrowserRuntimeError("CAD render preflight request failed") from exc
            except (OSError, urllib_error.URLError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25)
        if response_bytes is None:
            raise BrowserRuntimeError("CAD render preflight request failed") from last_error
        if len(response_bytes) > 16 * 1024 * 1024:
            raise BrowserRuntimeError("CAD render preflight response is too large")
        try:
            value = json.loads(response_bytes)
            result = value["result"]
            png_data_url = result["pngDataUrl"]
            png_bytes = base64.b64decode(png_data_url.split(",", 1)[1], validate=True)
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeError("CAD render preflight response is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema", "jobId", "program", "programDigest", "result"}
            or value["schema"] != "text-to-cad.cad-render-response/1"
            or value["jobId"] != self.owner_nonce
            or value["program"] != "residual"
            or value["programDigest"] != CAD_RENDER_PROGRAMS["residual"]
            or not isinstance(result, dict)
            or result.get("ok") is not True
            or not isinstance(png_data_url, str)
            or not png_data_url.startswith("data:image/png;base64,")
            or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            or not isinstance(result.get("views"), list)
            or len(result["views"]) != 8
        ):
            raise BrowserRuntimeError("CAD render preflight identity is invalid")
        self._publish_preflight_receipt(png_bytes)

    # -- internals --------------------------------------------------------

    def _build_run_argv(self) -> list[str]:
        return [
            "docker", "run", "--detach",
            "--name", self.container_name,
            "--network", self.network_name,
            "--publish", f"127.0.0.1:0:{self.container_port}/tcp",
            "--publish", f"127.0.0.1:0:{self.cad_render_container_port}/tcp",
            "--env", f"TTC_CAD_RENDER_TOKEN={self._cad_render_token}",
            "--env", f"TTC_BROWSER_RUNTIME_JOB_ID={self.owner_nonce}",
            "--env", (
                "TTC_CAD_RENDER_PROGRAMS_JSON="
                + json.dumps(dict(CAD_RENDER_PROGRAMS), separators=(",", ":"))
            ),
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
            "--tmpfs", "/home/pwuser:rw,size=64m,uid=1000",
            "--shm-size", self.shm_size,
            "--security-opt", "no-new-privileges",
            "--cpus", self.cpu_limit,
            "--memory", self.memory_limit,
            "--pids-limit", str(self.pids_limit),
            "--stop-timeout", "5",
            self.image_ref,
        ]

    def _discover_published_port(self, container_port: int) -> int:
        result = self.docker(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{ (index (index .NetworkSettings.Ports "
                    f'"{container_port}/tcp") 0).HostPort '
                    "}}"
                ),
                self.container_name,
            ]
        )
        raw = (result.stdout or "").strip()
        if not raw:
            raise BrowserRuntimeError(
                "docker did not report a published host port for "
                f"{container_port}/tcp on {self.container_name}"
            )
        try:
            port = int(raw)
        except ValueError as exc:
            raise BrowserRuntimeError(
                f"docker reported non-integer published port: {raw!r}"
            ) from exc
        if not (1 <= port <= 65535):
            raise BrowserRuntimeError(f"docker reported invalid published port: {port}")
        return port

    def _wait_for_port(self) -> None:
        import socket as _socket

        for label, port in (
            ("browser MCP", self._published_port),
            ("CAD render", self._cad_render_published_port),
        ):
            deadline = time.monotonic() + self.port_ready_timeout_s
            while time.monotonic() < deadline:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    try:
                        sock.connect(("127.0.0.1", port))
                        break
                    except (ConnectionRefusedError, OSError):
                        pass
                time.sleep(self.port_ready_poll_s)
            else:
                raise BrowserRuntimeError(
                    f"{label} port did not accept connections within "
                    f"{self.port_ready_timeout_s:.1f}s"
                )
        self._wait_for_cad_health()

    def _wait_for_cad_health(self) -> None:
        health_url = (
            f"http://127.0.0.1:{self._cad_render_published_port}/healthz"
        )
        deadline = time.monotonic() + self.port_ready_timeout_s
        while time.monotonic() < deadline:
            try:
                with _LOOPBACK_OPENER.open(health_url, timeout=2.0) as response:
                    value = json.loads(response.read(_MAX_HEALTH_RESPONSE_BYTES + 1))
                if (
                    isinstance(value, dict)
                    and value.get("schema") == "text-to-cad.cad-render-health/1"
                    and value.get("programs") == dict(CAD_RENDER_PROGRAMS)
                ):
                    return
            except (OSError, ValueError, urllib_error.URLError, json.JSONDecodeError):
                pass
            time.sleep(self.port_ready_poll_s)
        raise BrowserRuntimeError(
            "CAD render health endpoint did not become ready within "
            f"{self.port_ready_timeout_s:.1f}s"
        )

    def _publish_runtime_capability(self) -> None:
        capability = {
            "schema": RUNTIME_CAPABILITY_SCHEMA,
            "jobId": self.owner_nonce,
            "imageRef": self.image_ref,
            "mcpUrl": self.mcp_url,
            "cadRenderUrl": self.cad_render_url,
            "cadRenderToken": self._cad_render_token,
            "programs": dict(CAD_RENDER_PROGRAMS),
        }
        encoded = json.dumps(
            capability,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        target = self.capability_dir / SANDBOX_RUNTIME_CAPABILITY_NAME
        temporary = self.capability_dir / f".{SANDBOX_RUNTIME_CAPABILITY_NAME}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short capability write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(temporary, 0o444)
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise BrowserRuntimeError(
                "cannot publish browser runtime capability"
            ) from exc

    def _publish_preflight_receipt(self, png_bytes: bytes) -> None:
        receipt = {
            "schema": "text-to-cad.browser-runtime-preflight/1",
            "imageRef": self.image_ref,
            "program": "residual",
            "programDigest": CAD_RENDER_PROGRAMS["residual"],
            "pngSha256": "sha256:" + hashlib.sha256(png_bytes).hexdigest(),
            "passed": True,
        }
        target = self.capability_dir / "preflight.json"
        try:
            target.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                encoding="ascii",
            )
            target.chmod(0o444)
        except OSError as exc:
            raise BrowserRuntimeError("cannot publish CAD render preflight") from exc

    def _capture_render_ledger(self) -> tuple[bytes, int]:
        if self._cad_render_published_port is None:
            raise BrowserRuntimeError("CAD render audit endpoint is unavailable")
        audit_url = (
            f"http://127.0.0.1:{self._cad_render_published_port}"
            "/cad/audit/requests"
        )
        request = urllib_request.Request(
            audit_url,
            headers={"authorization": f"Bearer {self._cad_render_token}"},
            method="GET",
        )
        try:
            with _LOOPBACK_OPENER.open(request, timeout=10.0) as response:
                raw = response.read(_MAX_AUDIT_RESPONSE_BYTES + 1)
        except (OSError, urllib_error.URLError) as exc:
            raise BrowserRuntimeError("cannot collect browser runtime render ledger") from exc
        if not raw or len(raw) > _MAX_AUDIT_RESPONSE_BYTES:
            raise BrowserRuntimeError("browser runtime render ledger has an invalid size")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeError("browser runtime render ledger is invalid") from exc
        requests = value.get("requests") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "jobId", "programs", "requests"}
            or value.get("schema") != "text-to-cad.cad-render-request-ledger/1"
            or value.get("jobId") != self.owner_nonce
            or value.get("programs") != dict(CAD_RENDER_PROGRAMS)
            or not isinstance(requests, list)
            or len(requests) > _MAX_RENDER_LEDGER_ENTRIES
        ):
            raise BrowserRuntimeError("browser runtime render ledger identity is invalid")
        row_keys = {
            "sequence", "program", "programDigest", "requestBytes",
            "requestSha256", "responseStatus", "responseBytes",
            "responseSha256", "outcome",
        }
        for sequence, row in enumerate(requests):
            if (
                not isinstance(row, dict)
                or set(row) != row_keys
                or not isinstance(row.get("sequence"), int)
                or isinstance(row.get("sequence"), bool)
                or row.get("sequence") != sequence
                or row.get("program") not in CAD_RENDER_PROGRAMS
                or row.get("programDigest")
                != CAD_RENDER_PROGRAMS.get(row.get("program"))
                or not isinstance(row.get("requestBytes"), int)
                or isinstance(row.get("requestBytes"), bool)
                or row["requestBytes"] <= 0
                or row["requestBytes"] > _MAX_RENDER_REQUEST_BYTES
                or not isinstance(row.get("responseBytes"), int)
                or isinstance(row.get("responseBytes"), bool)
                or row["responseBytes"] <= 0
                or row["responseBytes"] > _MAX_RENDER_RESPONSE_BYTES
                or row.get("responseStatus") not in {200, 500}
                or not isinstance(row.get("requestSha256"), str)
                or _SHA256.fullmatch(row["requestSha256"]) is None
                or not isinstance(row.get("responseSha256"), str)
                or _SHA256.fullmatch(row["responseSha256"]) is None
                or row.get("outcome") not in {"succeeded", "render-failed"}
                or (row["responseStatus"] == 200) != (row["outcome"] == "succeeded")
            ):
                raise BrowserRuntimeError("browser runtime render ledger row is invalid")
        durable = {
            "schema": "text-to-cad.browser-runtime-render-ledger/1",
            "jobId": self.owner_nonce,
            "imageRef": self.image_ref,
            "programs": dict(CAD_RENDER_PROGRAMS),
            "requests": requests,
        }
        encoded = self._publish_json_receipt("render-ledger.json", durable)
        return encoded, len(requests)

    def _publish_cleanup_receipt(
        self,
        *,
        ledger_bytes: bytes | None,
        ledger_count: int | None,
        container_absent: bool,
        network_absent: bool,
        passed: bool,
    ) -> None:
        receipt = {
            "schema": "text-to-cad.browser-runtime-cleanup/1",
            "jobId": self.owner_nonce,
            "imageRef": self.image_ref,
            "containerName": self.container_name,
            "networkName": self.network_name,
            "renderLedgerSha256": (
                "sha256:" + hashlib.sha256(ledger_bytes).hexdigest()
                if ledger_bytes is not None
                else None
            ),
            "renderRequestCount": ledger_count,
            "containerAbsent": container_absent,
            "networkAbsent": network_absent,
            "passed": passed,
        }
        self._publish_json_receipt("cleanup.json", receipt)

    def _publish_json_receipt(self, name: str, value: dict[str, Any]) -> bytes:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        target = self.capability_dir / name
        temporary = self.capability_dir / f".{name}.tmp"
        try:
            self.capability_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short receipt write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(temporary, 0o444)
            os.replace(temporary, target)
            return encoded
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise BrowserRuntimeError(f"cannot publish browser runtime {name}") from exc

    def _docker_resource_absent(
        self,
        argv: Sequence[str],
        *,
        missing_markers: tuple[str, ...],
    ) -> bool:
        try:
            self.docker(argv)
        except FileNotFoundError as exc:
            raise BrowserRuntimeError("docker CLI unavailable during cleanup proof") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").lower()
            if any(marker in detail for marker in missing_markers):
                return True
            raise BrowserRuntimeError("docker cleanup absence proof failed") from exc
        return False

    def _docker_or_raise(self, argv: Sequence[str], *, purpose: str) -> None:
        try:
            self.docker(argv)
        except FileNotFoundError as exc:
            raise BrowserRuntimeError(
                f"docker CLI not available while trying to {purpose}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise BrowserRuntimeError(
                f"failed to {purpose}: exit={exc.returncode} "
                f"stderr={(exc.stderr or '').strip()[:400]}"
            ) from exc

    def _docker_ignore(self, argv: Sequence[str]) -> None:
        try:
            self.docker(argv)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass


# -- Codex MCP config staging ---------------------------------------------

def render_mcp_config(mcp_url: str) -> str:
    """Return the Codex ``config.toml`` fragment that dials ``mcp_url``.

    Codex accepts Streamable HTTP MCP servers via a ``url`` key. The MCP
    handshake stays entirely inside the sandbox's shared netns; the port
    is chosen per-job by Docker at ``start()`` time.
    """

    return (
        "[mcp_servers.browser]\n"
        f'url = "{mcp_url}"\n'
        'transport = "http"\n'
        'startup_timeout_ms = 15000\n'
    )
