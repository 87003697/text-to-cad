#!/usr/bin/env python3
"""Loopback proxy for narrowly retrying transient Venus failures."""

from __future__ import annotations

import http.client
import json
import math
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit


RETRYABLE_ERROR_CODE = "invalid_encrypted_content"
RETRYABLE_TRANSPORT_STATUS = 502
RETRYABLE_TRANSPORT_ERROR_CODE = "upstream_transport_error"
VENUS_CODEX_ROUTING_HEADER = "Venus-Codex-Routing"
MAX_RETRY_AFTER_SECONDS = 120.0
HANDLER_QUIESCE_TIMEOUT_SECONDS = 5.0
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class _ProxyServer(ThreadingHTTPServer):
    """HTTP server carrying immutable forwarding configuration."""

    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        target_url: str,
        audit_path: Path,
        backoffs: tuple[float, ...],
        rate_limit_backoffs: tuple[float, ...],
        transport_backoffs: tuple[float, ...],
        upstream_timeout: float,
        upstream_bearer_token: str | None,
        required_client_bearer_token: str | None,
        max_upstream_attempts: int | None,
    ) -> None:
        """Bind loopback on a dynamic port and retain retry policy."""

        if len(backoffs) > 2:
            raise ValueError("retry policy allows at most two extra attempts")
        if len(rate_limit_backoffs) > 2:
            raise ValueError("rate-limit retry policy allows at most two extra attempts")
        if len(transport_backoffs) > 2:
            raise ValueError("transport retry policy allows at most two extra attempts")
        if any(not math.isfinite(delay) or delay < 0 for delay in backoffs):
            raise ValueError("retry backoffs must be finite and non-negative")
        if any(
            not math.isfinite(delay) or delay < 0
            for delay in rate_limit_backoffs
        ):
            raise ValueError(
                "rate-limit retry backoffs must be finite and non-negative"
            )
        if any(
            not math.isfinite(delay) or delay < 0
            for delay in transport_backoffs
        ):
            raise ValueError(
                "transport retry backoffs must be finite and non-negative"
            )
        if not math.isfinite(upstream_timeout) or upstream_timeout <= 0:
            raise ValueError("upstream_timeout must be finite and positive")
        target = urlsplit(target_url)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        self.target = target
        self.audit_path = audit_path
        self.backoffs = backoffs
        self.rate_limit_backoffs = rate_limit_backoffs
        self.transport_backoffs = transport_backoffs
        self.upstream_timeout = upstream_timeout
        self.upstream_bearer_token = upstream_bearer_token
        self.required_client_bearer_token = required_client_bearer_token
        if max_upstream_attempts is not None and max_upstream_attempts <= 0:
            raise ValueError("max_upstream_attempts must be positive")
        self.max_upstream_attempts = max_upstream_attempts
        self.upstream_attempt_count = 0
        self.request_lock = threading.Lock()
        self.audit_lock = threading.Lock()
        self.stop_event = threading.Event()
        self._active_condition = threading.Condition()
        self._active_handlers = 0
        self._client_sockets: set[socket.socket] = set()
        self._upstream_connections: set[http.client.HTTPConnection] = set()
        super().__init__(("127.0.0.1", 0), _ProxyHandler)

    def begin_handler(self, connection: socket.socket) -> bool:
        """Register one request connection unless shutdown has started."""

        with self._active_condition:
            if self.stop_event.is_set():
                return False
            self._active_handlers += 1
            self._client_sockets.add(connection)
            return True

    def finish_handler(self, connection: socket.socket) -> None:
        """Forget one request connection and wake a waiting shutdown."""

        with self._active_condition:
            self._client_sockets.discard(connection)
            self._active_handlers -= 1
            self._active_condition.notify_all()

    def register_upstream_connection(
        self,
        connection: http.client.HTTPConnection,
    ) -> bool:
        """Register an upstream connection unless shutdown has started."""

        with self._active_condition:
            if self.stop_event.is_set():
                return False
            self._upstream_connections.add(connection)
            return True

    def unregister_upstream_connection(
        self,
        connection: http.client.HTTPConnection,
    ) -> None:
        """Forget one upstream connection after forwarding completes."""

        with self._active_condition:
            self._upstream_connections.discard(connection)

    def cancel_active(self) -> None:
        """Cancel retry waits and close every active forwarding connection."""

        with self._active_condition:
            self.stop_event.set()
            client_sockets = tuple(self._client_sockets)
            upstream_connections = tuple(self._upstream_connections)
        close_error: OSError | None = None
        for connection in upstream_connections:
            try:
                connection.close()
            except OSError as exc:
                if close_error is None:
                    close_error = exc
        for client_socket in client_sockets:
            try:
                client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client_socket.close()
            except OSError:
                pass
        if close_error is not None:
            raise close_error

    def wait_for_handlers(self, timeout: float) -> bool:
        """Wait until every active handler has exited, within a hard bound."""

        deadline = time.monotonic() + timeout
        with self._active_condition:
            while self._active_handlers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_condition.wait(remaining)
            return True

    def clear_credentials(self) -> None:
        """Drop bearer credentials once no handler can use them."""

        with self._active_condition:
            self.upstream_bearer_token = None
            self.required_client_bearer_token = None

    def target_path(self, incoming_path: str) -> str:
        """Replace the local /v1 prefix with the configured Venus base path."""

        suffix = incoming_path[3:] if incoming_path.startswith("/v1") else incoming_path
        return f"{self.target.path.rstrip('/')}/{suffix.lstrip('/')}"

    def record_attempt(
        self,
        attempt: int,
        status: int,
        error_code: str | None,
        retry_after_seconds: float | None,
    ) -> None:
        """Append a body-free, header-free retry audit record."""

        record = {
            "attempt": attempt,
            "status": status,
            "error_code": error_code,
        }
        if retry_after_seconds is not None:
            record["retry_after_seconds"] = retry_after_seconds
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_lock, self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")


class _ProxyHandler(BaseHTTPRequestHandler):
    """Forward one request and replay only the diagnosed transient failure."""

    server: _ProxyServer

    def handle(self) -> None:
        """Track this connection for cancellation during proxy shutdown."""

        if not self.server.begin_handler(self.connection):
            return
        try:
            super().handle()
        finally:
            self.server.finish_handler(self.connection)

    def do_POST(self) -> None:
        """Forward a Responses request, preserving its bytes across attempts."""

        if self.server.stop_event.is_set():
            return
        required_token = self.server.required_client_bearer_token
        if required_token is not None and self.headers.get("Authorization") != (
            f"Bearer {required_token}"
        ):
            response = b'{"error":{"code":"proxy_authentication_failed"}}'
            self._respond(
                401,
                [("Content-Type", "application/json")],
                response,
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, [("Content-Type", "application/json")], b"{}")
            return
        if content_length < 0:
            self._respond(400, [("Content-Type", "application/json")], b"{}")
            return
        body = self.rfile.read(content_length)
        excluded_headers = HOP_BY_HOP_HEADERS | {
            "host",
            "content-length",
            VENUS_CODEX_ROUTING_HEADER.lower(),
        }
        if self.server.upstream_bearer_token is not None:
            excluded_headers.add("authorization")
        forwarded_headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in excluded_headers
        }
        forwarded_headers["Content-Length"] = str(len(body))
        if self.server.upstream_bearer_token is not None:
            forwarded_headers["Authorization"] = (
                f"Bearer {self.server.upstream_bearer_token}"
            )
        # Venus documents this header as required for Codex encrypted
        # reasoning so every continuation stays on one resource provider.
        forwarded_headers[VENUS_CODEX_ROUTING_HEADER] = "true"

        retry_budget = max(
            len(self.server.backoffs),
            len(self.server.rate_limit_backoffs),
            len(self.server.transport_backoffs),
        )
        for attempt in range(1, retry_budget + 2):
            if self.server.stop_event.is_set():
                return
            with self.server.request_lock:
                if (
                    self.server.max_upstream_attempts is not None
                    and self.server.upstream_attempt_count
                    >= self.server.max_upstream_attempts
                ):
                    response = (
                        b'{"error":{"code":"proxy_request_budget_exhausted"}}'
                    )
                    self._respond(
                        429,
                        [("Content-Type", "application/json")],
                        response,
                    )
                    return
                self.server.upstream_attempt_count += 1
            status, headers, response_body = self._forward(
                body,
                forwarded_headers,
            )
            if self.server.stop_event.is_set():
                return
            error_code = self._error_code(status, response_body)
            retry_after_seconds = (
                self._retry_after_seconds(headers) if status == 429 else None
            )
            self.server.record_attempt(
                attempt,
                status,
                error_code,
                retry_after_seconds,
            )
            encrypted_content_failure = (
                status == 400
                and (
                    error_code == RETRYABLE_ERROR_CODE
                    or (
                        response_body == b""
                        and self._has_encrypted_reasoning(body)
                    )
                )
            )
            transport_failure = (
                status == RETRYABLE_TRANSPORT_STATUS
                and error_code == RETRYABLE_TRANSPORT_ERROR_CODE
            )
            retry_backoffs = (
                self.server.rate_limit_backoffs
                if status == 429
                else self.server.backoffs
                if encrypted_content_failure
                else self.server.transport_backoffs
                if transport_failure
                else ()
            )
            if attempt > len(retry_backoffs):
                self._respond(status, headers, response_body)
                return
            delay = retry_backoffs[attempt - 1]
            if retry_after_seconds is not None:
                delay = max(delay, retry_after_seconds)
            if self.server.stop_event.wait(delay):
                return

    def _forward(
        self,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        """Send one byte-identical request to the configured upstream."""

        target = self.server.target
        connection_type = (
            http.client.HTTPSConnection
            if target.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            target.hostname,
            target.port,
            timeout=self.server.upstream_timeout,
        )
        if not self.server.register_upstream_connection(connection):
            connection.close()
            return (
                503,
                [("Content-Type", "application/json")],
                b'{"error":{"code":"proxy_shutting_down"}}',
            )
        try:
            try:
                connection.request(
                    "POST",
                    self.server.target_path(self.path),
                    body=body,
                    headers=headers,
                )
                response = connection.getresponse()
                return response.status, response.getheaders(), response.read()
            except (OSError, http.client.HTTPException):
                response_body = json.dumps(
                    {"error": {"code": "upstream_transport_error"}},
                    separators=(",", ":"),
                ).encode()
                return (
                    502,
                    [("Content-Type", "application/json")],
                    response_body,
                )
        finally:
            self.server.unregister_upstream_connection(connection)
            connection.close()

    @staticmethod
    def _error_code(status: int, body: bytes) -> str | None:
        """Return the exact JSON error code eligible for retry."""

        try:
            payload = json.loads(body)
            code = payload.get("error", {}).get("code")
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return code if isinstance(code, str) else None

    @staticmethod
    def _retry_after_seconds(
        headers: list[tuple[str, str]],
    ) -> float | None:
        """Return one bounded numeric Retry-After hint without logging headers."""

        value = next(
            (
                raw.strip()
                for name, raw in headers
                if name.lower() == "retry-after"
            ),
            None,
        )
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return min(seconds, MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _has_encrypted_reasoning(body: bytes) -> bool:
        """Return whether this is an encrypted reasoning continuation."""

        try:
            payload = json.loads(body)
            items = payload.get("input")
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            return False
        if not isinstance(items, list):
            return False
        return any(
            isinstance(item, dict)
            and item.get("type") == "reasoning"
            and isinstance(item.get("encrypted_content"), str)
            and bool(item["encrypted_content"])
            for item in items
        )

    def _respond(
        self,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        """Return the final upstream response without hop-by-hop headers."""

        self.send_response(status)
        for name, value in headers:
            if name.lower() not in HOP_BY_HOP_HEADERS | {"content-length"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid request metadata leaking through the default access log."""


class RetryProxy:
    """Own one loopback retry proxy and its server thread."""

    def __init__(
        self,
        target_url: str,
        audit_path: Path,
        *,
        backoffs: tuple[float, ...] = (0.2, 0.5),
        rate_limit_backoffs: tuple[float, ...] = (10.0, 60.0),
        transport_backoffs: tuple[float, ...] = (0.5, 2.0),
        upstream_timeout: float = 180,
        upstream_bearer_token: str | None = None,
        required_client_bearer_token: str | None = None,
        max_upstream_attempts: int | None = None,
    ) -> None:
        """Configure a maximum of one initial attempt plus the backoffs."""

        self._server = _ProxyServer(
            target_url,
            audit_path,
            backoffs,
            rate_limit_backoffs,
            transport_backoffs,
            upstream_timeout,
            upstream_bearer_token,
            required_client_bearer_token,
            max_upstream_attempts,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="venus-retry-proxy",
            daemon=True,
        )

    @property
    def port(self) -> int:
        """Return the bound loopback port."""

        return self._server.server_port

    @property
    def url(self) -> str:
        """Return the target-compatible local base URL for claude-tap."""

        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> RetryProxy:
        """Start serving in the owning runner process."""

        self._thread.start()
        return self

    def stop(self) -> None:
        """Cancel handlers, then stop the loopback listener within a bound."""

        failure: BaseException | None = None

        def capture(operation) -> None:
            """Run cleanup while retaining the first failure for the caller."""

            nonlocal failure
            try:
                operation()
            except BaseException as exc:
                if failure is None:
                    failure = exc

        def join_server() -> None:
            """Join the serving thread without extending the shutdown bound."""

            self._thread.join(timeout=1)
            if self._thread.is_alive():
                raise TimeoutError("Venus retry proxy server did not stop")

        def wait_for_handlers() -> None:
            """Require every request handler to quiesce before returning."""

            if not self._server.wait_for_handlers(
                HANDLER_QUIESCE_TIMEOUT_SECONDS
            ):
                raise TimeoutError("Venus retry proxy handlers did not stop")

        try:
            capture(self._server.cancel_active)
            capture(self._server.shutdown)
            capture(join_server)
            capture(wait_for_handlers)
        finally:
            capture(self._server.server_close)
            self._server.clear_credentials()
        if failure is not None:
            raise failure

    def __enter__(self) -> RetryProxy:
        """Start the proxy for a bounded lifecycle."""

        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Stop the proxy and propagate caller failures."""

        self.stop()
        return False
