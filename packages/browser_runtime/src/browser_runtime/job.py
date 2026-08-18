"""Outer-owned lifecycle for a single per-job browser runtime container.

Each job creates:
- one Docker bridge network (``--internal``, no host reach) named
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

import json
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .config import load_image_lock


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
            # Prefer the immutable content ID; fall back to name:tag for
            # local dev builds that predate a locked digest.
            self.image_ref = (
                image.get("id")
                or f"{image['name']}:{image.get('tag', 'latest')}"
            )
        self._started = False
        self._published_port: int | None = None

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

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.capability_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._docker_or_raise(
            ["docker", "network", "create", self.network_name],
            purpose="create per-job docker network",
        )
        try:
            self._docker_or_raise(
                self._build_run_argv(),
                purpose="start browser runtime container",
            )
            self._published_port = self._discover_published_port()
        except BrowserRuntimeError:
            self._docker_ignore(
                ["docker", "rm", "--force", "--volumes", self.container_name]
            )
            self._docker_ignore(["docker", "network", "rm", self.network_name])
            raise
        self._started = True
        self._wait_for_port()

    def stop(self) -> None:
        self._docker_ignore(
            ["docker", "rm", "--force", "--volumes", self.container_name]
        )
        self._docker_ignore(["docker", "network", "rm", self.network_name])
        self._started = False
        self._published_port = None

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

    # -- internals --------------------------------------------------------

    def _build_run_argv(self) -> list[str]:
        return [
            "docker", "run", "--detach",
            "--name", self.container_name,
            "--network", self.network_name,
            "--publish", f"127.0.0.1:0:{self.container_port}/tcp",
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

    def _discover_published_port(self) -> int:
        result = self.docker(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{ (index (index .NetworkSettings.Ports "
                    f'"{self.container_port}/tcp") 0).HostPort '
                    "}}"
                ),
                self.container_name,
            ]
        )
        raw = (result.stdout or "").strip()
        if not raw:
            raise BrowserRuntimeError(
                "docker did not report a published host port for "
                f"{self.container_port}/tcp on {self.container_name}"
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

        deadline = time.monotonic() + self.port_ready_timeout_s
        while time.monotonic() < deadline:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                try:
                    sock.connect(("127.0.0.1", self._published_port))
                    return
                except (ConnectionRefusedError, OSError):
                    pass
            time.sleep(self.port_ready_poll_s)
        raise BrowserRuntimeError(
            f"browser runtime port 127.0.0.1:{self._published_port} did not "
            f"accept connections within {self.port_ready_timeout_s:.1f}s"
        )

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
