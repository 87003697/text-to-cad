"""Development-only, job-private Venus Responses proxy with paid-attempt accounting.

The proxy is intentionally narrower than SAI-010 Formal authority.  It accepts
one job capability, one model and one route, and writes body/header-free JSONL
evidence before any transport which may reach the provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
import fcntl
import hmac
import http.client
import json
import math
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit


VENUS_BASE_URL = "http://v2.open.venus.oa.com/llmproxy/v1"
MODEL = "gpt-5.6-sol"
RESPONSES_PATH = "/v1/responses"
LEDGER_SCHEMA = "text-to-cad.development-venus-ledger/1"
PRICING_AUTHORITY = "iWiki-4020336897-v54-2026-08-14"
MAX_ATTEMPTS = 48
LONG_CONTEXT_THRESHOLD = 272_000
USD_QUANTUM = Decimal("0.000001")
MILLION = Decimal(1_000_000)
RATES = {
    "short": {
        "input": Decimal("5"),
        "output": Decimal("30"),
        "cache_read": Decimal("0.5"),
        "cache_write": Decimal("6.25"),
    },
    "long": {
        "input": Decimal("10"),
        "output": Decimal("45"),
        "cache_read": Decimal("1"),
        "cache_write": Decimal("12.5"),
    },
}


def _money(value: Decimal) -> str:
    return str(value.quantize(USD_QUANTUM, rounding=ROUND_UP))


@dataclass(frozen=True)
class CostPolicy:
    """Immutable limits admitted before the proxy starts."""

    max_attempts: int = 16
    max_request_bytes: int = 200_000
    max_output_tokens: int = 40_000
    per_job_usd: Decimal = Decimal("100")
    total_usd: Decimal = Decimal("1000")
    max_jobs: int = 50

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise ValueError("max_attempts must be in [1, 48]")
        if self.max_request_bytes <= 0 or self.max_output_tokens <= 0:
            raise ValueError("request and output ceilings must be positive")
        if self.per_job_usd <= 0 or self.per_job_usd > Decimal("100"):
            raise ValueError("per-job USD ceiling must be in (0, 100]")
        if self.total_usd <= 0 or self.total_usd > Decimal("1000"):
            raise ValueError("total USD ceiling must be in (0, 1000]")
        if not 1 <= self.max_jobs <= 50:
            raise ValueError("max_jobs must be in [1, 50]")
        if self.worst_case_job_usd > self.per_job_usd:
            raise ValueError("configured attempts exceed the per-job USD ceiling")

    @property
    def rate_class(self) -> str:
        return "long" if self.max_request_bytes > LONG_CONTEXT_THRESHOLD else "short"

    @property
    def worst_case_attempt_usd(self) -> Decimal:
        rates = RATES[self.rate_class]
        # Every input byte is a conservative token upper bound and every such
        # token is reserved at the costliest applicable input class.
        return (
            Decimal(self.max_request_bytes) * rates["cache_write"]
            + Decimal(self.max_output_tokens) * rates["output"]
        ) / MILLION

    @property
    def worst_case_job_usd(self) -> Decimal:
        return self.worst_case_attempt_usd * self.max_attempts


def _settled_upper_bound(usage: dict[str, int]) -> Decimal:
    rate_class = "long" if usage["inputTokens"] > LONG_CONTEXT_THRESHOLD else "short"
    rates = RATES[rate_class]
    uncached = usage["inputTokens"] - usage["cachedInputTokens"]
    return (
        Decimal(uncached) * max(rates["input"], rates["cache_write"])
        + Decimal(usage["cachedInputTokens"]) * rates["cache_read"]
        + Decimal(usage["outputTokens"]) * rates["output"]
    ) / MILLION


class _Ledger:
    def __init__(self, job_path: Path, total_path: Path | None, policy: CostPolicy) -> None:
        self.job_path = job_path
        self.total_path = total_path or job_path
        self.policy = policy
        self._lock = threading.Lock()
        for path in {self.job_path, self.total_path}:
            if path.exists():
                with path.open("r", encoding="utf-8") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
                    self._exposure(self._read(stream), policy)
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _attempt_key(record: dict[str, object], event: str) -> tuple[str, int]:
        job_id = record.get("jobId")
        attempt = record.get("attempt")
        if not isinstance(job_id, str) or not job_id or len(job_id) > 128:
            raise RuntimeError(f"cost ledger {event} has invalid job identity")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt <= 0:
            raise RuntimeError(f"cost ledger {event} has invalid attempt identity")
        return job_id, attempt

    @staticmethod
    def _amount(record: dict[str, object], key: str, event: str) -> Decimal:
        try:
            amount = Decimal(str(record[key]))
        except (KeyError, ArithmeticError, ValueError) as error:
            raise RuntimeError(f"cost ledger {event} has invalid {key}") from error
        if not amount.is_finite() or amount < 0:
            raise RuntimeError(f"cost ledger {event} has invalid {key}")
        return amount

    @staticmethod
    def _validate_reserve(record: dict[str, object], policy: CostPolicy) -> Decimal:
        if record.get("schema") != LEDGER_SCHEMA:
            raise RuntimeError("cost ledger reserve has invalid schema")
        request_bytes = record.get("requestBytes")
        if (
            not isinstance(request_bytes, int) or isinstance(request_bytes, bool)
            or not 0 < request_bytes <= policy.max_request_bytes
        ):
            raise RuntimeError("cost ledger reserve has invalid request byte ceiling")
        expected = {
            "mayHaveReachedModel": True,
            "inputTokenUpperBound": policy.max_request_bytes,
            "outputTokenUpperBound": policy.max_output_tokens,
            "rateClass": policy.rate_class,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError("cost ledger reserve does not match the fixed token and rate policy")
        amount = _Ledger._amount(record, "reservedUsd", "reserve")
        if amount != Decimal(_money(policy.worst_case_attempt_usd)):
            raise RuntimeError("cost ledger reserve does not match the worst-case reservation")
        return amount

    @staticmethod
    def _validate_settle(record: dict[str, object], policy: CostPolicy) -> Decimal:
        if record.get("schema") != LEDGER_SCHEMA:
            raise RuntimeError("cost ledger settle has invalid schema")
        usage = record.get("usage")
        if not isinstance(usage, dict):
            raise RuntimeError("cost ledger settle has invalid usage")
        values = tuple(usage.get(key) for key in ("inputTokens", "outputTokens", "cachedInputTokens"))
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
            raise RuntimeError("cost ledger settle has invalid usage")
        input_tokens, output_tokens, cached_tokens = values
        if cached_tokens > input_tokens or input_tokens > policy.max_request_bytes or output_tokens > policy.max_output_tokens:
            raise RuntimeError("cost ledger settle usage exceeds the fixed token policy")
        if (
            record.get("actualUsd") is not None
            or record.get("actualUsdUnavailableReason") != "trusted_provider_dollar_telemetry_absent"
            or record.get("pricingAuthority") != PRICING_AUTHORITY
        ):
            raise RuntimeError("cost ledger settle has invalid pricing authority")
        expected = _settled_upper_bound(usage)
        settled = _Ledger._amount(record, "settledCostUpperBoundUsd", "settle")
        if settled != expected.quantize(USD_QUANTUM, rounding=ROUND_UP):
            raise RuntimeError("cost ledger settled cost does not match fixed rates and usage")
        return settled

    @staticmethod
    def _exposure(records: list[dict[str, object]], policy: CostPolicy) -> Decimal:
        exposure = Decimal(0)
        reserves: dict[tuple[str, int], Decimal] = {}
        settlements: set[tuple[str, int]] = set()
        for record in records:
            event = record.get("event")
            if event == "reserve":
                key = _Ledger._attempt_key(record, "reserve")
                if key in reserves:
                    raise RuntimeError("cost ledger contains duplicate reserve")
                amount = _Ledger._validate_reserve(record, policy)
                reserves[key] = amount
                exposure += amount
            elif event == "settle":
                key = _Ledger._attempt_key(record, "settle")
                if key not in reserves:
                    raise RuntimeError("cost ledger settle has no matching reserve")
                if key in settlements:
                    raise RuntimeError("cost ledger contains duplicate settle")
                released = _Ledger._amount(record, "releasedReservedUsd", "settle")
                settled = _Ledger._amount(record, "settledCostUpperBoundUsd", "settle")
                if released != reserves[key]:
                    raise RuntimeError("cost ledger settle does not release its exact reserve")
                if settled > released:
                    raise RuntimeError("cost ledger settlement exceeds its reserve")
                settled = _Ledger._validate_settle(record, policy)
                settlements.add(key)
                exposure -= released
                exposure += settled
        return exposure

    @staticmethod
    def _read(stream) -> list[dict[str, object]]:
        stream.seek(0)
        records = []
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError("cost ledger is not valid JSONL") from error
            if not isinstance(value, dict):
                raise RuntimeError("cost ledger row is not an object")
            records.append(value)
        return records

    @staticmethod
    def _append_locked(stream, record: dict[str, object]) -> None:
        stream.seek(0, 2)
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

    def reserve(self, record: dict[str, object], *, policy: CostPolicy) -> None:
        self.total_path.parent.mkdir(parents=True, exist_ok=True)
        self.job_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.total_path.open("a+", encoding="utf-8") as total:
            fcntl.flock(total.fileno(), fcntl.LOCK_EX)
            total_records = self._read(total)
            amount = Decimal(str(record["reservedUsd"]))
            admitted_jobs = {str(r["jobId"]) for r in total_records if r.get("event") == "reserve" and "jobId" in r}
            if record["jobId"] not in admitted_jobs and len(admitted_jobs) >= policy.max_jobs:
                raise PermissionError("total job ceiling exhausted")
            if self._exposure(total_records, policy) + amount > policy.total_usd:
                raise PermissionError("total USD reservation ceiling exhausted")
            if self.job_path == self.total_path:
                job_records = total_records
            else:
                with self.job_path.open("a+", encoding="utf-8") as job:
                    job_records = self._read(job)
            if self._exposure([r for r in job_records if r.get("jobId") == record["jobId"]], policy) + amount > policy.per_job_usd:
                raise PermissionError("per-job USD reservation ceiling exhausted")
            self._append_locked(total, record)
            if self.job_path != self.total_path:
                with self.job_path.open("a", encoding="utf-8") as job:
                    self._append_locked(job, record)
            fcntl.flock(total.fileno(), fcntl.LOCK_UN)

    def append(self, record: dict[str, object]) -> None:
        paths = [self.job_path] if self.job_path == self.total_path else [self.total_path, self.job_path]
        with self._lock:
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                    self._append_locked(stream, record)
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class _Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, owner: "DevelopmentProxy", bind_host: str, bind_port: int) -> None:
        self.owner = owner
        super().__init__((bind_host, bind_port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._reply(404, b'{"error":"route_denied"}')
            return
        self._reply(200, b'{"status":"Development/Not Sealed/Not Formal"}')

    def do_POST(self) -> None:
        owner = self.server.owner
        if self.path != RESPONSES_PATH:
            self._reply(404, b'{"error":"route_denied"}')
            return
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {owner.client_token}"):
            self._reply(401, b'{"error":"client_capability_denied"}')
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply(400, b'{"error":"content_length_required"}')
            return
        if length <= 0 or length > owner.policy.max_request_bytes:
            self._reply(413, b'{"error":"request_byte_ceiling"}')
            return
        body = self.rfile.read(length)
        try:
            request = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, b'{"error":"invalid_json"}')
            return
        if not isinstance(request, dict) or request.get("model") != MODEL:
            self._reply(400, b'{"error":"model_denied"}')
            return
        requested_output = request.get("max_output_tokens", owner.policy.max_output_tokens)
        if not isinstance(requested_output, int) or isinstance(requested_output, bool) or requested_output <= 0:
            self._reply(400, b'{"error":"invalid_max_output_tokens"}')
            return
        request["max_output_tokens"] = min(requested_output, owner.policy.max_output_tokens)
        forwarded_body = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        if len(forwarded_body) > owner.policy.max_request_bytes:
            self._reply(413, b'{"error":"request_byte_ceiling"}')
            return
        try:
            attempt = owner.reserve_attempt(len(forwarded_body))
        except PermissionError as error:
            code = "attempt_ceiling" if owner.attempts >= owner.policy.max_attempts else "cost_ceiling"
            self._reply(429, json.dumps({"error": code}, separators=(",", ":")).encode())
            return
        try:
            status, headers, response_body = owner.forward(forwarded_body)
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            owner.record_transport_error(attempt, type(error).__name__)
            self._reply(502, b'{"error":"upstream_transport_error"}')
            return
        usage = owner.parse_usage(response_body, dict((k.lower(), v) for k, v in headers))
        if usage is not None:
            owner.settle(attempt, usage)
        else:
            owner.record_missing_usage(attempt, status)
        safe_headers = [(k, v) for k, v in headers if k.lower() in {"content-type"}]
        self._reply(status, response_body, safe_headers)

    def _reply(self, status: int, body: bytes, headers=()) -> None:
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, *_args: object) -> None:
        pass


class DevelopmentProxy:
    """Own one bounded Development proxy lifecycle."""

    def __init__(
        self,
        target_url: str,
        ledger_path: Path,
        *,
        upstream_token: str | None,
        client_token: str,
        job_id: str,
        policy: CostPolicy = CostPolicy(),
        upstream_timeout: float = 180,
        bind_host: str = "127.0.0.1",
        bind_port: int = 0,
        total_ledger_path: Path | None = None,
        allow_mock_target: bool = False,
    ) -> None:
        target = urlsplit(target_url)
        if target_url != VENUS_BASE_URL and not allow_mock_target:
            raise ValueError("only the fixed Venus base is allowed")
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("target must be an absolute HTTP(S) URL")
        if not client_token or len(client_token) > 4096 or "\n" in client_token:
            raise ValueError("client token is invalid")
        if not job_id or len(job_id) > 128:
            raise ValueError("job id is invalid")
        if not math.isfinite(upstream_timeout) or upstream_timeout <= 0:
            raise ValueError("upstream timeout must be positive")
        self.target = target
        self.ledger = _Ledger(ledger_path, total_ledger_path, policy)
        self.upstream_token = upstream_token
        self.client_token = client_token
        self.job_id = job_id
        self.policy = policy
        self.upstream_timeout = upstream_timeout
        self.attempts = 0
        self.unresolved = Decimal(0)
        self._state_lock = threading.Lock()
        self._server = _Server(self, bind_host, bind_port)
        self._thread = threading.Thread(target=self._server.serve_forever, name=f"venus-proxy-{job_id}", daemon=True)
        self._stopped = False

    @property
    def port(self) -> int:
        return self._server.server_port

    @property
    def url(self) -> str:
        return f"http://{self._server.server_address[0]}:{self.port}/v1"

    def reserve_attempt(self, request_bytes: int) -> int:
        with self._state_lock:
            if self.attempts >= self.policy.max_attempts:
                raise PermissionError("attempt ceiling exhausted")
            attempt = self.attempts + 1
            record = {
                "schema": LEDGER_SCHEMA,
                "event": "reserve",
                "jobId": self.job_id,
                "attempt": attempt,
                "mayHaveReachedModel": True,
                "requestBytes": request_bytes,
                "inputTokenUpperBound": self.policy.max_request_bytes,
                "outputTokenUpperBound": self.policy.max_output_tokens,
                "rateClass": self.policy.rate_class,
                "reservedUsd": _money(self.policy.worst_case_attempt_usd),
            }
            self.ledger.reserve(record, policy=self.policy)
            self.attempts = attempt
            self.unresolved += self.policy.worst_case_attempt_usd
            return attempt

    def forward(self, body: bytes):
        cls = http.client.HTTPSConnection if self.target.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.target.hostname, self.target.port, timeout=self.upstream_timeout)
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body)), "Venus-Codex-Routing": "true"}
        if self.upstream_token is not None:
            headers["Authorization"] = f"Bearer {self.upstream_token}"
        path = f"{self.target.path.rstrip('/')}/responses"
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    @staticmethod
    def parse_usage(body: bytes, headers: dict[str, str]) -> dict[str, int] | None:
        payloads = []
        if headers.get("content-type", "").split(";", 1)[0].strip() == "text/event-stream":
            for line in body.splitlines():
                if line.startswith(b"data: ") and line[6:] != b"[DONE]":
                    try:
                        payloads.append(json.loads(line[6:]))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        else:
            try:
                payloads.append(json.loads(body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
        for payload in reversed(payloads):
            if not isinstance(payload, dict):
                continue
            usage = payload.get("usage")
            if usage is None and isinstance(payload.get("response"), dict):
                usage = payload["response"].get("usage")
            if not isinstance(usage, dict):
                continue
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            details = usage.get("input_tokens_details", {})
            cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
            if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (input_tokens, output_tokens, cached)) and cached <= input_tokens:
                return {"inputTokens": input_tokens, "outputTokens": output_tokens, "cachedInputTokens": cached}
        return None

    def settle(self, attempt: int, usage: dict[str, int]) -> None:
        if (
            usage["inputTokens"] > self.policy.max_request_bytes
            or usage["outputTokens"] > self.policy.max_output_tokens
        ):
            self.record_missing_usage(attempt, 200, reason="usage_exceeds_token_ceiling")
            return
        settled_upper_bound = _settled_upper_bound(usage)
        reserved = self.policy.worst_case_attempt_usd
        # Usage which contradicts the enforced reservation is not trusted.
        if settled_upper_bound > reserved:
            self.record_missing_usage(attempt, 200, reason="usage_exceeds_reservation")
            return
        with self._state_lock:
            self.unresolved -= reserved
        self.ledger.append({
            "schema": "text-to-cad.development-venus-ledger/1", "event": "settle",
            "jobId": self.job_id, "attempt": attempt, "usage": usage,
            "actualUsd": None,
            "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent",
            "settledCostUpperBoundUsd": _money(settled_upper_bound),
            "releasedReservedUsd": _money(reserved),
            "pricingAuthority": PRICING_AUTHORITY,
        })

    def record_transport_error(self, attempt: int, category: str) -> None:
        self.ledger.append({
            "schema": "text-to-cad.development-venus-ledger/1", "event": "transport-error",
            "jobId": self.job_id, "attempt": attempt, "category": category,
            "reservationReleased": False,
        })

    def record_missing_usage(self, attempt: int, status: int, *, reason: str = "trusted_usage_missing") -> None:
        self.ledger.append({
            "schema": "text-to-cad.development-venus-ledger/1", "event": "usage-unresolved",
            "jobId": self.job_id, "attempt": attempt, "status": status,
            "reason": reason, "reservationReleased": False,
        })

    def start(self) -> "DevelopmentProxy":
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._stopped:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        listener_absent = True
        probe = socket.socket()
        probe.settimeout(0.05)
        try:
            listener_absent = probe.connect_ex(self._server.server_address) != 0
        finally:
            probe.close()
        self.ledger.append({
            "schema": "text-to-cad.development-venus-ledger/1", "event": "terminal",
            "jobId": self.job_id, "attempts": self.attempts,
            "unresolvedReservedUsd": _money(self.unresolved),
            "listenerAbsent": listener_absent,
            "classification": "Development/Not Sealed/Not Formal",
        })
        self._stopped = True

    def __enter__(self) -> "DevelopmentProxy":
        return self.start()

    def __exit__(self, _kind: type[BaseException] | None, _value: BaseException | None, _traceback: TracebackType | None) -> bool:
        self.stop()
        return False
