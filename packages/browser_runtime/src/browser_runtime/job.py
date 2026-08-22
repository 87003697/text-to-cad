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
import stat
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

from .config import (
    CAD_RENDER_PROGRAMS,
    RUNTIME_CAPABILITY_SCHEMA,
    SANDBOX_RUNTIME_CAPABILITY_NAME,
)


_LOOPBACK_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))
_MAX_HEALTH_RESPONSE_BYTES = 4096
_MAX_AUDIT_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_RENDER_LEDGER_ENTRIES = 4096
_MAX_RENDER_REQUEST_BYTES = 160 * 1024 * 1024
_MAX_RENDER_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_MCP_RESPONSE_BYTES = 8 * 1024 * 1024
_EXACT_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BARE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_RETENTION_REFERENCE = re.compile(
    r"text-to-cad-browser-runtime-retained:[0-9a-f]{64}\Z"
)
VIEWER_SMOKE_URL = (
    "http://127.0.0.1:9225/?file=spur_gear_blank.glb"
)
VIEWER_SMOKE_DOCUMENT = "spur_gear_blank.glb"


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
    viewer_runtime_dir: Path | None = None
    docker: DockerRunner = field(default=_run_docker)
    port_ready_timeout_s: float = 20.0
    port_ready_poll_s: float = 0.15
    cleanup_absence_timeout_s: float = 5.0
    cleanup_absence_poll_s: float = 0.1
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
        self._image_lock_bytes: bytes | None = None
        self._image_lock: dict[str, Any] | None = None
        if self.image_ref is None:
            lock_path = Path(self.image_lock_path) if self.image_lock_path else None
            if lock_path is None:
                from .config import IMAGE_LOCK_PATH

                lock_path = IMAGE_LOCK_PATH
            try:
                self._image_lock_bytes = lock_path.read_bytes()
                lock = json.loads(self._image_lock_bytes)
                image = lock["image"]
                self.image_ref = image["id"]
            except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
                raise BrowserRuntimeError("browser runtime image lock is invalid") from exc
            if not isinstance(lock, dict):
                raise BrowserRuntimeError("browser runtime image lock is invalid")
            self._image_lock = lock
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
        self.viewer_runtime_dir = (
            Path(self.viewer_runtime_dir).resolve()
            if self.viewer_runtime_dir is not None
            else None
        )
        self._viewer_model_dir: Path | None = None
        self._viewer_document_sha256: str | None = None

    # -- factory ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        exp_dir: Path,
        *,
        owner_nonce: str | None = None,
        image_ref: str | None = None,
        image_lock_path: Path | None = None,
        viewer_runtime_dir: Path | None = None,
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
        if viewer_runtime_dir is not None:
            kwargs["viewer_runtime_dir"] = viewer_runtime_dir
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
        if self.viewer_runtime_dir is not None:
            self._stage_viewer_smoke_document()
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
            if self.viewer_runtime_dir is not None:
                self._start_viewer()
            self._publish_runtime_capability()
            self._publish_image_authority()
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

        try:
            container_absent = self._remove_and_wait_absent(
                remove_argv=[
                    "docker", "rm", "--force", "--volumes", self.container_name,
                ],
                inspect_argv=["docker", "container", "inspect", self.container_name],
                missing_markers=("no such object", "no such container"),
            )
        except BrowserRuntimeError as exc:
            errors.append(str(exc))
            container_absent = False
        try:
            network_absent = self._remove_and_wait_absent(
                remove_argv=["docker", "network", "rm", self.network_name],
                inspect_argv=["docker", "network", "inspect", self.network_name],
                missing_markers=(
                    "no such network",
                    f"network {self.network_name.lower()} not found",
                ),
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

    def preflight_mcp(self, viewer_url: str = VIEWER_SMOKE_URL) -> None:
        """Prove MCP tool discovery and a real production Viewer page before paid work."""

        if self._viewer_document_sha256 is None:
            raise BrowserRuntimeError("CAD Viewer smoke document is unavailable")
        parsed = urlsplit(viewer_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 9225
            or parsed.path != "/"
            or parsed.query != "file=spur_gear_blank.glb"
            or parsed.fragment
        ):
            raise BrowserRuntimeError("CAD Viewer smoke URL is invalid")
        initialized, session = self._mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "text-to-cad-browser-runtime-preflight",
                        "version": "1",
                    },
                },
            }
        )
        result = initialized.get("result") if isinstance(initialized, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("protocolVersion") != "2025-03-26"
            or not session
        ):
            raise BrowserRuntimeError("Browser MCP initialize response is invalid")
        self._mcp_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session=session,
        )
        listed, session = self._mcp_post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session=session,
        )
        listed_result = listed.get("result") if isinstance(listed, dict) else None
        tools = listed_result.get("tools") if isinstance(listed_result, dict) else None
        if not isinstance(tools, list):
            raise BrowserRuntimeError("Browser MCP tool list is invalid")
        tool_names = tuple(
            item.get("name")
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        required_tools = {
            "browser_navigate",
            "browser_run_code_unsafe",
            "browser_snapshot",
            "browser_take_screenshot",
        }
        if len(tool_names) != len(tools) or not required_tools.issubset(tool_names):
            raise BrowserRuntimeError("Browser MCP required tools are unavailable")

        deadline = time.monotonic() + 30.0
        navigation: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            response, session = self._mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "browser_navigate",
                        "arguments": {"url": viewer_url},
                    },
                },
                session=session,
            )
            navigation = self._mcp_tool_result(response)
            if navigation is not None:
                break
            time.sleep(0.25)
        if navigation is None:
            raise BrowserRuntimeError("Browser MCP could not open the CAD Viewer")

        ready_response, session = self._mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "browser_run_code_unsafe",
                    "arguments": {
                        "code": (
                            "async (page) => {"
                            "const button=page.getByRole('button',{name:'Copy screenshot'});"
                            "await button.waitFor({state:'visible',timeout:30000});"
                            "await page.waitForFunction(()=>{const b=document.querySelector("
                            "'button[aria-label=\"Copy screenshot\"]');return b&&!b.disabled;},"
                            "null,{timeout:30000});"
                            "return {url:page.url(),screenshotEnabled:!(await button.isDisabled())};"
                            "}"
                        )
                    },
                },
            },
            session=session,
        )
        ready = self._mcp_tool_result(ready_response)
        ready_text = self._mcp_text(ready)
        ready_value = self._mcp_json_object(ready_text)
        if (
            ready is None
            or ready_value
            != {"url": viewer_url, "screenshotEnabled": True}
        ):
            raise BrowserRuntimeError("CAD Viewer model did not become ready")

        snapshot_response, session = self._mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "browser_snapshot", "arguments": {}},
            },
            session=session,
        )
        snapshot = self._mcp_tool_result(snapshot_response)
        snapshot_text = self._mcp_text(snapshot)
        if snapshot is None or "Copy screenshot" not in snapshot_text:
            raise BrowserRuntimeError("CAD Viewer accessibility snapshot is invalid")

        screenshot_response, _session = self._mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "browser_take_screenshot",
                    "arguments": {"type": "png"},
                },
            },
            session=session,
        )
        screenshot = self._mcp_tool_result(screenshot_response)
        png_bytes = self._mcp_image(screenshot)
        if screenshot is None or png_bytes is None or not png_bytes.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise BrowserRuntimeError("CAD Viewer screenshot is invalid")
        receipt = {
            "schema": "text-to-cad.browser-runtime-mcp-smoke/1",
            "jobId": self.owner_nonce,
            "imageRef": self.image_ref,
            "protocolVersion": result["protocolVersion"],
            "toolNames": sorted(tool_names),
            "viewerUrl": viewer_url,
            "viewerDocument": VIEWER_SMOKE_DOCUMENT,
            "viewerDocumentSha256": self._viewer_document_sha256,
            "modelReady": True,
            "snapshotSha256": "sha256:"
            + hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest(),
            "screenshotSha256": "sha256:" + hashlib.sha256(png_bytes).hexdigest(),
            "passed": True,
        }
        self._publish_json_receipt("mcp-smoke.json", receipt)

    def _mcp_post(
        self,
        value: dict[str, Any],
        *,
        session: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        if session is not None:
            headers["mcp-session-id"] = session
        request = urllib_request.Request(
            self.mcp_url,
            data=json.dumps(value, separators=(",", ":")).encode("ascii"),
            headers=headers,
            method="POST",
        )
        try:
            with _LOOPBACK_OPENER.open(request, timeout=40.0) as response:
                raw = response.read(_MAX_MCP_RESPONSE_BYTES + 1)
                next_session = response.headers.get("mcp-session-id") or session
        except (OSError, urllib_error.URLError) as exc:
            raise BrowserRuntimeError("Browser MCP request failed") from exc
        if len(raw) > _MAX_MCP_RESPONSE_BYTES:
            raise BrowserRuntimeError("Browser MCP response is too large")
        if not raw:
            return {}, next_session
        try:
            text = raw.decode("utf-8")
            if text.lstrip().startswith("{"):
                decoded = json.loads(text)
            else:
                payloads = [
                    json.loads(line.removeprefix("data:").strip())
                    for line in text.splitlines()
                    if line.startswith("data:")
                ]
                decoded = payloads[-1]
        except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeError("Browser MCP response is invalid") from exc
        if not isinstance(decoded, dict):
            raise BrowserRuntimeError("Browser MCP response is invalid")
        return decoded, next_session

    @staticmethod
    def _mcp_tool_result(response: dict[str, Any]) -> dict[str, Any] | None:
        result = response.get("result")
        if not isinstance(result, dict) or result.get("isError") is True:
            return None
        return result

    @staticmethod
    def _mcp_text(result: dict[str, Any] | None) -> str:
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            return ""
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )

    @staticmethod
    def _mcp_image(result: dict[str, Any] | None) -> bytes | None:
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            return None
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "image"
                and item.get("mimeType") == "image/png"
                and isinstance(item.get("data"), str)
            ):
                try:
                    return base64.b64decode(item["data"], validate=True)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _mcp_json_object(text: str) -> dict[str, Any] | None:
        """Decode the final standalone JSON object from MCP text content."""

        for line in reversed(text.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    # -- internals --------------------------------------------------------

    def _build_run_argv(self) -> list[str]:
        argv = [
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
        ]
        if self.viewer_runtime_dir is not None and self._viewer_model_dir is not None:
            viewer_document = self._viewer_model_dir / VIEWER_SMOKE_DOCUMENT
            required = (
                self.viewer_runtime_dir / "backend/server.mjs",
                self.viewer_runtime_dir / "dist/index.html",
            )
            if (
                any(not path.is_file() for path in required)
                or not self._viewer_model_dir.is_dir()
                or not self._is_self_consistent_glb(viewer_document)
            ):
                raise BrowserRuntimeError("CAD Viewer runtime assets are unavailable")
            if "," in os.fspath(self.viewer_runtime_dir) or "," in os.fspath(
                self._viewer_model_dir
            ):
                raise BrowserRuntimeError(
                    "CAD Viewer runtime paths contain an unsupported delimiter"
                )
            argv.extend(
                [
                    "--mount",
                    "type=bind,source="
                    + os.fspath(self.viewer_runtime_dir)
                    + ",target=/opt/text-to-cad/viewer,readonly",
                    "--mount",
                    "type=bind,source="
                    + os.fspath(self._viewer_model_dir)
                    + ",target=/opt/text-to-cad/viewer-models,readonly",
                ]
            )
        argv.append(self.image_ref)
        return argv

    def _stage_viewer_smoke_document(self) -> None:
        """Create one fixed native GLB without relying on repository LFS state."""

        model_dir = self.capability_dir / "viewer-smoke-assets"
        target = model_dir / VIEWER_SMOKE_DOCUMENT
        temporary = model_dir / f".{VIEWER_SMOKE_DOCUMENT}.tmp"
        glb = self._viewer_smoke_glb()
        temporary_owned = False
        try:
            try:
                model_dir.mkdir(mode=0o750)
            except FileExistsError:
                metadata = model_dir.lstat()
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(
                    metadata.st_mode
                ):
                    raise OSError("CAD Viewer smoke asset root is invalid")
                entries = {entry.name: entry for entry in model_dir.iterdir()}
                allowed_entries = {
                    VIEWER_SMOKE_DOCUMENT,
                    temporary.name,
                }
                if set(entries) - allowed_entries:
                    raise OSError("CAD Viewer smoke asset root is not closed")
                if temporary.name in entries:
                    temporary_metadata = temporary.lstat()
                    if (
                        not stat.S_ISREG(temporary_metadata.st_mode)
                        or stat.S_ISLNK(temporary_metadata.st_mode)
                    ):
                        raise OSError("CAD Viewer smoke temporary is invalid")
                    os.chmod(model_dir, 0o750)
                    temporary.unlink()
                if VIEWER_SMOKE_DOCUMENT in entries:
                    target_metadata = target.lstat()
                    if (
                        not stat.S_ISREG(target_metadata.st_mode)
                        or stat.S_ISLNK(target_metadata.st_mode)
                        or target.read_bytes() != glb
                    ):
                        raise OSError("CAD Viewer smoke document conflicts")
                    os.chmod(target, 0o444)
                    os.chmod(model_dir, 0o555)
                    self._viewer_model_dir = model_dir.resolve()
                    self._viewer_document_sha256 = (
                        "sha256:" + hashlib.sha256(glb).hexdigest()
                    )
                    return
                os.chmod(model_dir, 0o750)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
            temporary_owned = True
            try:
                payload = memoryview(glb)
                while payload:
                    written = os.write(descriptor, payload)
                    if written <= 0:
                        raise OSError("short CAD Viewer smoke document write")
                    payload = payload[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, target)
            temporary_owned = False
            os.chmod(target, 0o444)
            os.chmod(model_dir, 0o555)
        except OSError as exc:
            if temporary_owned:
                try:
                    os.chmod(model_dir, 0o750)
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise BrowserRuntimeError(
                "cannot stage CAD Viewer smoke document"
            ) from exc
        self._viewer_model_dir = model_dir.resolve()
        self._viewer_document_sha256 = (
            "sha256:" + hashlib.sha256(glb).hexdigest()
        )

    @staticmethod
    def _viewer_smoke_glb() -> bytes:
        positions = struct.pack(
            "<9f",
            -0.6,
            -0.45,
            0.0,
            0.6,
            -0.45,
            0.0,
            0.0,
            0.65,
            0.0,
        )
        indices = struct.pack("<3H", 0, 1, 2)
        binary = positions + indices
        binary += b"\0" * (-len(binary) % 4)
        document = {
            "asset": {"version": "2.0", "generator": "text-to-cad-viewer-smoke"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "meshes": [
                {
                    "primitives": [
                        {"attributes": {"POSITION": 0}, "indices": 1}
                    ]
                }
            ],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 3,
                    "type": "VEC3",
                    "min": [-0.6, -0.45, 0.0],
                    "max": [0.6, 0.65, 0.0],
                },
                {
                    "bufferView": 1,
                    "componentType": 5123,
                    "count": 3,
                    "type": "SCALAR",
                },
            ],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                {
                    "buffer": 0,
                    "byteOffset": len(positions),
                    "byteLength": len(indices),
                    "target": 34963,
                },
            ],
            "buffers": [{"byteLength": len(positions) + len(indices)}],
        }
        json_chunk = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        json_chunk += b" " * (-len(json_chunk) % 4)
        total_length = 12 + 8 + len(json_chunk) + 8 + len(binary)
        return b"".join(
            (
                struct.pack("<4sII", b"glTF", 2, total_length),
                struct.pack("<I4s", len(json_chunk), b"JSON"),
                json_chunk,
                struct.pack("<I4s", len(binary), b"BIN\0"),
                binary,
            )
        )

    @staticmethod
    def _is_self_consistent_glb(path: Path) -> bool:
        """Reject missing files and unhydrated LFS pointers before Docker starts."""

        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                header = stream.read(12)
        except OSError:
            return False
        if len(header) != 12:
            return False
        magic, version, declared_size = struct.unpack("<4sII", header)
        return magic == b"glTF" and version == 2 and declared_size == size

    def _start_viewer(self) -> None:
        self._docker_or_raise(
            [
                "docker",
                "exec",
                "--detach",
                self.container_name,
                "node",
                "/opt/text-to-cad/viewer/backend/server.mjs",
                "--host",
                "127.0.0.1",
                "--port",
                "9225",
                "--port-scan-limit",
                "0",
                "--dir",
                "/opt/text-to-cad/viewer-models",
            ],
            purpose="start the production CAD Viewer",
        )

    def _publish_image_authority(self) -> None:
        if self._image_lock is None or self._image_lock_bytes is None:
            return
        source_revision = self._image_lock.get("built_from_ref")
        image = self._image_lock.get("image")
        host = self._image_lock.get("host")
        expected_lock_keys = {"schema_version", "image", "built_from_ref", "notes"}
        if host is not None:
            expected_lock_keys.add("host")
        expected_image_keys = {
            "name",
            "id",
            "base_image",
            "base_id",
            "playwright_mcp_version",
            "content_size_bytes",
            "architecture",
        }
        if (
            set(self._image_lock) != expected_lock_keys
            or self._image_lock.get("schema_version") != 1
            or not isinstance(self._image_lock.get("notes"), str)
            or not isinstance(image, dict)
            or set(image) != expected_image_keys
            or image.get("name") != "text-to-cad-browser-runtime"
            or image.get("id") != self.image_ref
            or not isinstance(image.get("base_image"), str)
            or not isinstance(image.get("base_id"), str)
            or _EXACT_IMAGE_ID.fullmatch(image["base_id"]) is None
            or not isinstance(image.get("playwright_mcp_version"), str)
            or not isinstance(image.get("content_size_bytes"), int)
            or image["content_size_bytes"] <= 0
            or image.get("architecture") != "amd64"
            or not isinstance(source_revision, str)
            or _REVISION.fullmatch(source_revision) is None
            or (host is not None and not isinstance(host, dict))
        ):
            raise BrowserRuntimeError("browser runtime image authority is invalid")
        if isinstance(host, dict):
            source_image_ref = host.get("sourceImageId")
            retention_reference = host.get("retentionReference")
            archive_sha256 = host.get("archiveSha256")
            if (
                set(host)
                != {"sourceImageId", "retentionReference", "archiveSha256"}
                or not isinstance(source_image_ref, str)
                or _EXACT_IMAGE_ID.fullmatch(source_image_ref) is None
                or not isinstance(retention_reference, str)
                or _RETENTION_REFERENCE.fullmatch(retention_reference) is None
                or retention_reference.rsplit(":", 1)[1]
                != self.image_ref.removeprefix("sha256:")
                or not isinstance(archive_sha256, str)
                or _BARE_SHA256.fullmatch(archive_sha256) is None
            ):
                raise BrowserRuntimeError("browser runtime image authority is invalid")
        authority_kind = "host-lock" if isinstance(host, dict) else "repository-lock"
        receipt = {
            "schema": "text-to-cad.browser-runtime-image-authority/1",
            "jobId": self.owner_nonce,
            "imageRef": self.image_ref,
            "authorityKind": authority_kind,
            "lockSha256": "sha256:"
            + hashlib.sha256(self._image_lock_bytes).hexdigest(),
            "sourceRevision": source_revision,
            "sourceImageRef": (
                host.get("sourceImageId")
                if isinstance(host, dict)
                else self.image_ref
            ),
            "retentionReference": (
                host.get("retentionReference") if isinstance(host, dict) else None
            ),
            "archiveSha256": (
                host.get("archiveSha256") if isinstance(host, dict) else None
            ),
        }
        self._publish_json_receipt("image-authority.json", receipt)

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

    def _remove_and_wait_absent(
        self,
        *,
        remove_argv: Sequence[str],
        inspect_argv: Sequence[str],
        missing_markers: tuple[str, ...],
    ) -> bool:
        deadline = time.monotonic() + self.cleanup_absence_timeout_s
        while True:
            self._docker_ignore(remove_argv)
            if self._docker_resource_absent(
                inspect_argv,
                missing_markers=missing_markers,
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.cleanup_absence_poll_s)

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
