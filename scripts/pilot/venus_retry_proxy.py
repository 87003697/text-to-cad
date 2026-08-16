#!/usr/bin/env python3
"""Loopback proxy for narrowly retrying transient Venus decryption failures."""

from __future__ import annotations

import http.client
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit


RETRYABLE_ERROR_CODE = "invalid_encrypted_content"
VENUS_CODEX_ROUTING_HEADER = "Venus-Codex-Routing"
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
        upstream_timeout: float,
        upstream_bearer_token: str | None,
        required_client_bearer_token: str | None,
    ) -> None:
        """Bind loopback on a dynamic port and retain retry policy."""

        if len(backoffs) > 2:
            raise ValueError("retry policy allows at most two extra attempts")
        if any(not math.isfinite(delay) or delay < 0 for delay in backoffs):
            raise ValueError("retry backoffs must be finite and non-negative")
        if not math.isfinite(upstream_timeout) or upstream_timeout <= 0:
            raise ValueError("upstream_timeout must be finite and positive")
        target = urlsplit(target_url)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        self.target = target
        self.audit_path = audit_path
        self.backoffs = backoffs
        self.upstream_timeout = upstream_timeout
        self.upstream_bearer_token = upstream_bearer_token
        self.required_client_bearer_token = required_client_bearer_token
        self.audit_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _ProxyHandler)

    def target_path(self, incoming_path: str) -> str:
        """Replace the local /v1 prefix with the configured Venus base path."""

        suffix = incoming_path[3:] if incoming_path.startswith("/v1") else incoming_path
        return f"{self.target.path.rstrip('/')}/{suffix.lstrip('/')}"

    def record_attempt(self, attempt: int, status: int, error_code: str | None) -> None:
        """Append a body-free, header-free retry audit record."""

        record = {
            "attempt": attempt,
            "status": status,
            "error_code": error_code,
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_lock, self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")


class _ProxyHandler(BaseHTTPRequestHandler):
    """Forward one request and replay only the diagnosed transient failure."""

    server: _ProxyServer

    def do_POST(self) -> None:
        """Forward a Responses request, preserving its bytes across attempts."""

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
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

        for attempt in range(1, len(self.server.backoffs) + 2):
            status, headers, response_body = self._forward(
                body,
                forwarded_headers,
            )
            error_code = self._error_code(status, response_body)
            self.server.record_attempt(attempt, status, error_code)
            retryable = (
                status == 400
                and (
                    error_code == RETRYABLE_ERROR_CODE
                    or (
                        response_body == b""
                        and self._has_encrypted_reasoning(body)
                    )
                )
            )
            if not retryable or attempt > len(self.server.backoffs):
                self._respond(status, headers, response_body)
                return
            time.sleep(self.server.backoffs[attempt - 1])

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
        upstream_timeout: float = 180,
        upstream_bearer_token: str | None = None,
        required_client_bearer_token: str | None = None,
    ) -> None:
        """Configure a maximum of one initial attempt plus the backoffs."""

        self._server = _ProxyServer(
            target_url,
            audit_path,
            backoffs,
            upstream_timeout,
            upstream_bearer_token,
            required_client_bearer_token,
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
        """Stop accepting requests and close the loopback listener."""

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

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
