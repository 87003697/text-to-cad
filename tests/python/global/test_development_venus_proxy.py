from __future__ import annotations

import http.client
import importlib.util
import json
from decimal import Decimal
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class MockUpstream:
    def __init__(self, responses, *, delay: float = 0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.requests: list[tuple[str, bytes, str | None]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append((self.path, body, self.headers.get("Authorization")))
                time.sleep(owner.delay)
                status, content_type, payload = owner.responses.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except BrokenPipeError:
                    pass

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/llmproxy/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


def post(port: int, body: bytes, token: str = "job-token"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    connection.request(
        "POST", "/v1/responses", body=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, payload


class DevelopmentVenusProxyTests(unittest.TestCase):
    def test_cli_secret_reference_accepts_one_lf_but_rejects_empty_or_crlf(self):
        script = Path("scripts/pilot/agent-runtime-development-proxy.py").resolve()
        spec = importlib.util.spec_from_file_location("development_proxy_cli", script)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("a-valid-token\n", encoding="utf-8")
            self.assertEqual(module._secret(path, "test"), "a-valid-token")
            for invalid in ("", "token\r\n", "token\nextra"):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid"):
                    module._secret(path, "test")

    def test_recommended_policy_independently_reserves_39_20(self):
        from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy

        policy = CostPolicy(max_attempts=16, max_request_bytes=200_000, max_output_tokens=40_000)
        self.assertEqual(policy.worst_case_attempt_usd, Decimal("2.450000"))
        self.assertEqual(policy.worst_case_job_usd, Decimal("39.200000"))

    def test_dispatch_clamps_output_reserves_before_upstream_and_accounts_usage(self):
        response = json.dumps({
            "id": "response-1",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "input_tokens_details": {"cached_tokens": 10},
            },
        }).encode()
        request = json.dumps({"model": "gpt-5.6-sol", "input": "secret-request-body-marker", "max_output_tokens": 999999}).encode()
        with tempfile.TemporaryDirectory() as directory, MockUpstream(
            [(200, "application/json", response)]
        ) as upstream:
            from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy, DevelopmentProxy

            ledger = Path(directory) / "ledger.jsonl"
            with DevelopmentProxy(
                upstream.url, ledger, upstream_token="provider-token", client_token="job-token",
                job_id="cup-cup-033-job-1", policy=CostPolicy(max_attempts=1),
                allow_mock_target=True,
            ) as proxy:
                status, payload = post(proxy.port, request)
            ledger_text = ledger.read_text()
            records = [json.loads(line) for line in ledger_text.splitlines()]

        self.assertEqual((status, payload), (200, response))
        forwarded = json.loads(upstream.requests[0][1])
        self.assertEqual(forwarded["max_output_tokens"], 40_000)
        self.assertEqual(upstream.requests[0][0], "/llmproxy/v1/responses")
        self.assertEqual(upstream.requests[0][2], "Bearer provider-token")
        self.assertEqual([record["event"] for record in records], ["reserve", "settle", "terminal"])
        self.assertEqual(records[0]["mayHaveReachedModel"], True)
        self.assertEqual(records[0]["reservedUsd"], "2.450000")
        self.assertEqual(records[1]["usage"]["inputTokens"], 100)
        self.assertEqual(records[1]["settledCostUpperBoundUsd"], "0.001168")
        self.assertIsNone(records[1]["actualUsd"])
        self.assertEqual(
            records[1]["actualUsdUnavailableReason"],
            "trusted_provider_dollar_telemetry_absent",
        )
        self.assertNotIn("secret-request-body-marker", ledger_text)
        self.assertNotIn("provider-token", ledger_text)

    def test_missing_usage_and_timeout_keep_reservations(self):
        request = b'{"model":"gpt-5.6-sol","input":"x","max_output_tokens":1}'
        with tempfile.TemporaryDirectory() as directory, MockUpstream(
            [(200, "application/json", b'{"id":"no-usage"}'), (200, "application/json", b'{"late":true}')],
            delay=0.06,
        ) as upstream:
            from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy, DevelopmentProxy

            ledger = Path(directory) / "ledger.jsonl"
            with DevelopmentProxy(
                upstream.url, ledger, upstream_token=None, client_token="job-token",
                job_id="cup-cup-033-job-2", policy=CostPolicy(max_attempts=2),
                upstream_timeout=0.01, allow_mock_target=True,
            ) as proxy:
                first = post(proxy.port, request)
                second = post(proxy.port, request)
            records = [json.loads(line) for line in ledger.read_text().splitlines()]

        self.assertEqual(first[0], 502)
        self.assertEqual(second[0], 502)
        self.assertEqual([r["event"] for r in records], ["reserve", "transport-error", "reserve", "transport-error", "terminal"])
        self.assertEqual(records[-1]["unresolvedReservedUsd"], "4.900000")

    def test_cross_job_token_wrong_route_model_and_49th_attempt_are_denied(self):
        request = b'{"model":"gpt-5.6-sol","input":"x","max_output_tokens":1}'
        responses = [(200, "application/json", b'{"usage":{"input_tokens":1,"output_tokens":1}}')] * 48
        with tempfile.TemporaryDirectory() as directory, MockUpstream(responses) as upstream:
            from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy, DevelopmentProxy

            with DevelopmentProxy(
                upstream.url, Path(directory) / "ledger.jsonl", upstream_token=None,
                client_token="job-token", job_id="cup-cup-033-job-3",
                policy=CostPolicy(max_attempts=48, max_request_bytes=100, max_output_tokens=1), allow_mock_target=True,
            ) as proxy:
                wrong_token = post(proxy.port, request, "other-job-token")
                wrong_model = post(proxy.port, b'{"model":"other","input":"x"}')
                connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=2)
                connection.request("POST", "/v1/chat/completions", body=request, headers={"Authorization": "Bearer job-token"})
                wrong_route = connection.getresponse().status
                connection.close()
                statuses = [post(proxy.port, request)[0] for _ in range(49)]

        self.assertEqual(wrong_token[0], 401)
        self.assertEqual(wrong_model[0], 400)
        self.assertEqual(wrong_route, 404)
        self.assertEqual(statuses, [200] * 48 + [429])
        self.assertEqual(len(upstream.requests), 48)

    def test_sse_usage_is_parsed_and_listener_is_absent_after_cleanup(self):
        sse = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":3}}}\n\ndata: [DONE]\n\n'
        request = b'{"model":"gpt-5.6-sol","input":"x","max_output_tokens":3}'
        with tempfile.TemporaryDirectory() as directory, MockUpstream(
            [(200, "text/event-stream", sse)]
        ) as upstream:
            from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy, DevelopmentProxy

            ledger = Path(directory) / "ledger.jsonl"
            proxy = DevelopmentProxy(
                upstream.url, ledger, upstream_token=None, client_token="job-token",
                job_id="cup-cup-033-job-4", policy=CostPolicy(max_attempts=1),
                allow_mock_target=True,
            )
            proxy.start()
            port = proxy.port
            self.assertEqual(post(port, request)[0], 200)
            proxy.stop()
            with self.assertRaises((ConnectionRefusedError, OSError)):
                post(port, request)
            records = [json.loads(line) for line in ledger.read_text().splitlines()]

        self.assertEqual(records[1]["usage"]["outputTokens"], 3)
        self.assertTrue(records[-1]["listenerAbsent"])

    def test_usage_above_enforced_ceiling_is_untrusted_and_keeps_reservation(self):
        response = b'{"usage":{"input_tokens":200001,"output_tokens":1}}'
        request = b'{"model":"gpt-5.6-sol","input":"x","max_output_tokens":1}'
        with tempfile.TemporaryDirectory() as directory, MockUpstream(
            [(200, "application/json", response)]
        ) as upstream:
            from scripts.pilot.agent_runtime.development_venus_proxy import CostPolicy, DevelopmentProxy

            ledger = Path(directory) / "ledger.jsonl"
            with DevelopmentProxy(
                upstream.url, ledger, upstream_token=None, client_token="job-token",
                job_id="cup-cup-033-job-5", policy=CostPolicy(max_attempts=1),
                allow_mock_target=True,
            ) as proxy:
                self.assertEqual(post(proxy.port, request)[0], 200)
            records = [json.loads(line) for line in ledger.read_text().splitlines()]

        self.assertEqual(records[1]["reason"], "usage_exceeds_token_ceiling")
        self.assertEqual(records[-1]["unresolvedReservedUsd"], "2.450000")


if __name__ == "__main__":
    unittest.main()
