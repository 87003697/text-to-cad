"""Unix-socket bridge from the isolated Agent to the trusted W3 handler."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
from typing import Any


MAX_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
SOCKET_TARGET = "/run/mesh-to-cad-agent-surface.sock"


def _load_mcp_module() -> Any:
    root = Path(__file__).resolve().parents[2]
    surface = root / "skills/mesh-to-cad/scripts/mesh-to-cad-agent-surface"
    if str(surface) not in sys.path:
        sys.path.insert(0, str(surface))
    handler_name = "mesh_to_cad_agent_surface_handler"
    handler = sys.modules.get(handler_name)
    if handler is None:
        handler_spec = importlib.util.spec_from_file_location(
            handler_name, surface / "handler.py"
        )
        if handler_spec is None or handler_spec.loader is None:
            raise RuntimeError("Agent Surface handler is unavailable")
        handler = importlib.util.module_from_spec(handler_spec)
        sys.modules[handler_name] = handler
        sys.modules["handler"] = handler
        handler_spec.loader.exec_module(handler)
    else:
        sys.modules["handler"] = handler
    name = "mesh_to_cad_agent_surface_mcp"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, surface / "mcp.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Agent Surface MCP adapter is unavailable")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


class AgentSurfaceBridge:
    """Serve CLI envelopes and MCP frames without exposing supervisor state."""

    def __init__(self, surface: Any, socket_path: Path) -> None:
        self.surface = surface
        self.socket_path = Path(socket_path).resolve()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        self._mcp = _load_mcp_module()

    def start(self) -> None:
        if self.socket_path.exists() or self.socket_path.is_symlink():
            if self.socket_path.is_symlink() or not self.socket_path.is_socket():
                raise RuntimeError("Agent Surface socket path is unsafe")
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(os.fspath(self.socket_path))
            self.socket_path.chmod(0o600)
            server.listen(2)
            server.settimeout(0.2)
        except OSError:
            server.close()
            self.socket_path.unlink(missing_ok=True)
            raise
        self._server = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        cancellation_error: Exception | None = None
        callback = getattr(self.surface, "cancel", None)
        if callback is not None:
            try:
                callback()
            except Exception as error:
                cancellation_error = error
        if self._server is not None:
            self._server.close()
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._connections_lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=5)
        alive = [worker for worker in workers if worker.is_alive()]
        self.socket_path.unlink(missing_ok=True)
        self._server = None
        self._thread = None
        if cancellation_error is not None and not alive and callback is not None:
            # Closing the client transport can let an already-cancelled
            # handler unwind.  Confirm that drain before treating the first
            # bounded cancellation attempt as a lifetime failure.
            try:
                callback()
            except Exception as error:
                cancellation_error = error
            else:
                cancellation_error = None
        if alive:
            raise RuntimeError("Agent Surface handler threads did not terminate")
        if cancellation_error is not None:
            raise RuntimeError("Agent Surface cancellation did not complete") from cancellation_error

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except (OSError, socket.timeout):
                continue
            worker = threading.Thread(
                target=self._connection_worker,
                args=(connection,),
                daemon=True,
            )
            with self._connections_lock:
                self._workers.add(worker)
            worker.start()

    def _connection_worker(self, connection: socket.socket) -> None:
        with connection:
            with self._connections_lock:
                self._connections.add(connection)
            try:
                self._serve_connection(connection)
            except (OSError, TypeError, ValueError):
                # A malformed or abruptly closed client is scoped to this
                # connection; the trusted bridge keeps serving later calls.
                pass
            finally:
                with self._connections_lock:
                    self._connections.discard(connection)
                    self._workers.discard(threading.current_thread())

    def _serve_connection(self, connection: socket.socket) -> None:
        stream = connection.makefile("rwb")
        state = self._mcp.PRE_INIT
        try:
            while not self._stop.is_set():
                raw = stream.readline(MAX_FRAME_BYTES + 1)
                if not raw:
                    return
                if len(raw) > MAX_FRAME_BYTES:
                    self._write(stream, {"ok": False, "error": "request_too_large"})
                    return
                try:
                    request = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._write(stream, {"ok": False, "error": "invalid_request"})
                    continue
                if not isinstance(request, dict):
                    self._write(stream, {"ok": False, "error": "invalid_request"})
                    continue
                if request.get("jsonrpc") == "2.0":
                    frame, state = self._mcp_frame(request, state)
                    if frame is not None:
                        self._write(stream, frame)
                    else:
                        self._write(stream, {"__notification__": True})
                    continue
                try:
                    response = self.surface.handle(request)
                    frame = {"ok": True, "response": response}
                except Exception as error:
                    try:
                        from handler import AgentSurfaceError, error_document
                    except ImportError:
                        frame = {"ok": False, "error": "supervisor_failure"}
                    else:
                        if isinstance(error, AgentSurfaceError):
                            frame = {"ok": False, **error_document(error)}
                        else:
                            frame = {"ok": False, "error": "supervisor_failure"}
                self._write(stream, frame)
        finally:
            stream.close()

    def _mcp_frame(self, request: dict[str, Any], state: str) -> tuple[Any, str]:
        if "id" not in request:
            return self._mcp._handle_request(self.surface, request, state)
        if not self._mcp._valid_request_id(request.get("id")):
            return (
                self._mcp._rpc_error(
                    None, self._mcp.INVALID_REQUEST, "request id is invalid"
                ),
                state,
            )
        return self._mcp._handle_request(self.surface, request, state)

    @staticmethod
    def _write(stream: Any, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        stream.write(payload)
        stream.write(b"\n")
        stream.flush()


__all__ = ["AgentSurfaceBridge", "MAX_FRAME_BYTES", "SOCKET_TARGET"]
