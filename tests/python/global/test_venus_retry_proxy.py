from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


class ScriptedUpstream:
    """Serve scripted responses while retaining the requests it received."""

    def __init__(
        self,
        responses: list[
            tuple[int, bytes] | tuple[int, bytes, dict[str, str]]
        ],
        *,
        delay: float = 0,
    ) -> None:
        self.responses = responses
        self.delay = delay
        self.requests: list[tuple[str, bytes, str | None]] = []
        self.request_headers: list[list[tuple[str, str]]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            """Capture one request and return the next scripted response."""

            def do_POST(self) -> None:
                """Record raw request evidence without interpreting its body."""

                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append(
                    (self.path, body, self.headers.get("Authorization"))
                )
                owner.request_headers.append(list(self.headers.items()))
                time.sleep(owner.delay)
                scripted = owner.responses.pop(0)
                status, response = scripted[:2]
                response_headers = scripted[2] if len(scripted) == 3 else {}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in response_headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                try:
                    self.wfile.write(response)
                except BrokenPipeError:
                    pass

            def log_message(self, format: str, *args: object) -> None:
                """Keep focused tests quiet."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

    @property
    def url(self) -> str:
        """Return the replacement-compatible Venus base URL."""

        return f"http://127.0.0.1:{self.server.server_port}/llmproxy/v1"

    def __enter__(self) -> ScriptedUpstream:
        """Start accepting requests."""

        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Stop the local upstream and propagate test failures."""

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        return False


class VenusRetryProxyTests(unittest.TestCase):
    """Verify the retry proxy through its loopback HTTP interface."""

    def test_stop_cancels_inflight_rate_limit_backoff(self) -> None:
        from scripts.pilot.venus_retry_proxy import RetryProxy

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            request_error: list[BaseException] = []
            with ScriptedUpstream(
                [
                    (429, b'{"error":{"code":"rate_limited"}}'),
                    (200, b'{"id":"must-not-be-sent"}'),
                ]
            ) as upstream:
                proxy = RetryProxy(
                    upstream.url,
                    audit_path,
                    rate_limit_backoffs=(2.0,),
                    upstream_bearer_token="upstream-token",
                    required_client_bearer_token="client-token",
                )
                proxy.start()

                def send_request() -> None:
                    try:
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", proxy.port, timeout=3
                        )
                        connection.request(
                            "POST",
                            "/v1/responses",
                            body=b"{}",
                            headers={"Authorization": "Bearer client-token"},
                        )
                        response = connection.getresponse()
                        response.read()
                        connection.close()
                    except BaseException as error:
                        request_error.append(error)

                request_thread = threading.Thread(target=send_request)
                request_thread.start()
                deadline = time.monotonic() + 2
                while len(upstream.requests) < 1 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(1, len(upstream.requests))

                started = time.monotonic()
                proxy.stop()
                elapsed = time.monotonic() - started
                request_thread.join(timeout=1)

                self.assertLess(elapsed, 1)
                self.assertFalse(request_thread.is_alive())
                self.assertEqual(1, len(upstream.requests))
                self.assertEqual(0, proxy._server._active_handlers)
                self.assertIsNone(proxy._server.upstream_bearer_token)
                self.assertIsNone(proxy._server.required_client_bearer_token)
                self.assertEqual(
                    "Bearer upstream-token", upstream.requests[0][2]
                )
                self.assertLessEqual(len(request_error), 1)

    def test_tap_gateway_uses_client_token_and_proxy_replaces_upstream(self) -> None:
        from scripts.pilot.venus_retry_proxy import RetryProxy

        gateway = Path(__file__).resolve().parents[3] / "gateway/codex-tap-gpt56"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = root / "codex-capture.json"
            audit_path = root / "venus-retry.jsonl"
            fake_codex = root / "codex"
            fake_codex.write_text(
                f"#!{sys.executable}\n"
                "import json\n"
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                "from urllib.request import Request, urlopen\n"
                "\n"
                "args = sys.argv[1:]\n"
                "key = 'model_providers.venus.experimental_bearer_token'\n"
                "token = None\n"
                "for index, arg in enumerate(args[:-1]):\n"
                "    if arg in ('-c', '--config') and args[index + 1].startswith(key + '='):\n"
                "        token = json.loads(args[index + 1].split('=', 1)[1])\n"
                "        break\n"
                "if token is None or 'VENUS_TOKEN' in os.environ:\n"
                "    raise SystemExit(17)\n"
                "request = Request(\n"
                "    os.environ['CLAUDE_TAP_URL'] + '/responses',\n"
                "    data=b'{}',\n"
                "    headers={'Authorization': 'Bearer ' + token},\n"
                ")\n"
                "with urlopen(request, timeout=3) as response:\n"
                "    response.read()\n"
                "Path(os.environ['CAPTURE']).write_text(\n"
                "    json.dumps({'argv': args, 'env': sorted(os.environ)}),\n"
                "    encoding='utf-8',\n"
                ")\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            environment = {
                key: value
                for key, value in os.environ.items()
                if key != "VENUS_TOKEN"
            }
            environment.update(
                {
                    "PATH": f"{root}:{environment.get('PATH', '')}",
                    "CAPTURE": str(capture),
                    "CLAUDE_TAP_CLIENT_TOKEN": "client-token",
                }
            )
            with ScriptedUpstream([(200, b'{"id":"upstream-ok"}')]) as upstream:
                with RetryProxy(
                    upstream.url,
                    audit_path,
                    upstream_bearer_token="upstream-token",
                    required_client_bearer_token="client-token",
                ) as proxy:
                    environment["CLAUDE_TAP_URL"] = proxy.url
                    completed = subprocess.run(
                        [str(gateway), "gpt-5.5", "exec", "prompt"],
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    for authorization in (None, "Bearer wrong-client-token"):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", proxy.port, timeout=2
                        )
                        headers = (
                            {}
                            if authorization is None
                            else {"Authorization": authorization}
                        )
                        connection.request(
                            "POST", "/v1/responses", body=b"{}", headers=headers
                        )
                        response = connection.getresponse()
                        response.read()
                        connection.close()
                        self.assertEqual(401, response.status)

            captured = json.loads(capture.read_text(encoding="utf-8"))
            self.assertNotIn("VENUS_TOKEN", captured["env"])
            self.assertIn("client-token", json.dumps(captured["argv"]))
            self.assertIn("gpt-5.5", captured["argv"])
            self.assertNotIn("upstream-token", json.dumps(captured))
            self.assertNotIn("upstream-token", completed.stdout + completed.stderr)
            self.assertEqual("Bearer upstream-token", upstream.requests[0][2])
            self.assertEqual(1, len(upstream.requests))

    def test_root_side_token_replaces_untrusted_client_authorization(self) -> None:
        success = b'{"id":"response-ok"}'
        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(200, success)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    upstream_bearer_token="root-held-token",
                    required_client_bearer_token="one-time-client-token",
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", proxy.port, timeout=2
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=b"{}",
                        headers={
                            "Authorization": "Bearer one-time-client-token"
                        },
                    )
                    response = connection.getresponse()
                    response.read()
                    connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(upstream.requests[0][2], "Bearer root-held-token")

    def test_root_side_proxy_rejects_an_unrecognized_local_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(200, b"{}")]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    upstream_bearer_token="root-held-token",
                    required_client_bearer_token="one-time-client-token",
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", proxy.port, timeout=2
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=b"{}",
                        headers={"Authorization": "Bearer wrong"},
                    )
                    response = connection.getresponse()
                    response.read()
                    connection.close()

        self.assertEqual(response.status, 401)
        self.assertEqual(upstream.requests, [])

    def test_request_budget_rejects_calls_before_they_reach_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(200, b"{}")]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url, audit_path, max_upstream_attempts=1
                ) as proxy:
                    statuses = []
                    for _ in range(2):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", proxy.port, timeout=2
                        )
                        connection.request("POST", "/v1/responses", body=b"{}")
                        response = connection.getresponse()
                        response.read()
                        statuses.append(response.status)
                        connection.close()

        self.assertEqual(statuses, [200, 429])
        self.assertEqual(len(upstream.requests), 1)

    def test_rate_limit_replays_exact_request_after_bounded_backoff(self) -> None:
        limited = b'{"error":{"message":"Too Many Requests"}}'
        success = b'{"id":"response-after-rate-limit"}'
        request_body = b'{"input":[{"type":"message","content":"continue"}]}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(429, limited), (200, success)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    rate_limit_backoffs=(0,),
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", proxy.port, timeout=2
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=request_body,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()
            audit = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual((response.status, response_body), (200, success))
        self.assertEqual([item[1] for item in upstream.requests], [request_body] * 2)
        self.assertEqual(
            [(item["attempt"], item["status"]) for item in audit],
            [(1, 429), (2, 200)],
        )

    def test_rate_limit_honors_bounded_retry_after_hint(self) -> None:
        limited = b'{"error":{"message":"Too Many Requests"}}'
        success = b'{"id":"response-after-retry-after"}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream(
                [
                    (429, limited, {"Retry-After": "25"}),
                    (200, success),
                ]
            ) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                proxy = RetryProxy(
                    upstream.url,
                    audit_path,
                    rate_limit_backoffs=(7,),
                )
                with mock.patch.object(
                    proxy._server.stop_event, "wait", return_value=False
                ) as wait:
                    with proxy:
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", proxy.port, timeout=2
                        )
                        connection.request("POST", "/v1/responses", body=b"{}")
                        response = connection.getresponse()
                        response_body = response.read()
                        connection.close()

        self.assertEqual((response.status, response_body), (200, success))
        self.assertIn(mock.call(25.0), wait.call_args_list)

    def test_default_rate_limit_backoffs_cover_a_minute_window(self) -> None:
        from scripts.pilot.venus_retry_proxy import RetryProxy

        with tempfile.TemporaryDirectory() as temp:
            proxy = RetryProxy(
                "http://127.0.0.1:1/llmproxy/v1",
                Path(temp) / "venus-retry.jsonl",
            )
            try:
                self.assertGreaterEqual(
                    sum(proxy._server.rate_limit_backoffs), 60
                )
            finally:
                proxy._server.server_close()

    def test_empty_400_retries_only_encrypted_reasoning_continuation(self) -> None:
        request_body = json.dumps(
            {
                "input": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "opaque-continuation",
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()
        success = b'{"id":"response-after-empty-400"}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(400, b""), (200, success)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    backoffs=(0,),
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=request_body,
                        headers={
                            "Authorization": "Bearer secret-token",
                            "Venus-Codex-Routing": "false",
                        },
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()
            audit = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual((response.status, response_body), (200, success))
        self.assertEqual(len(upstream.requests), 2)
        self.assertEqual(upstream.requests[0][1], request_body)
        self.assertEqual(upstream.requests[1][1], request_body)
        self.assertEqual(
            [
                dict(headers).get("Venus-Codex-Routing")
                for headers in upstream.request_headers
            ],
            ["true", "true"],
        )
        self.assertEqual(
            audit,
            [
                {"attempt": 1, "status": 400, "error_code": None},
                {"attempt": 2, "status": 200, "error_code": None},
            ],
        )

    def test_empty_400_without_encrypted_reasoning_is_not_retried(self) -> None:
        request_body = b'{"input":[{"type":"message","content":"hello"}]}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(400, b"")]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(upstream.url, audit_path) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=request_body,
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()

        self.assertEqual((response.status, response_body), (400, b""))
        self.assertEqual(len(upstream.requests), 1)

    def test_retryable_encrypted_content_failure_replays_exact_request(self) -> None:
        retryable = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_encrypted_content",
                }
            }
        ).encode()
        success = b'{"id":"response-ok"}'
        request_body = b'{"input":[{"encrypted_content":"opaque"}]}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(400, retryable), (200, success)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    backoffs=(0,),
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=request_body,
                        headers={
                            "Authorization": "Bearer secret-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()

        self.assertEqual((response.status, response_body), (200, success))
        self.assertEqual(
            upstream.requests,
            [
                (
                    "/llmproxy/v1/responses",
                    request_body,
                    "Bearer secret-token",
                ),
                (
                    "/llmproxy/v1/responses",
                    request_body,
                    "Bearer secret-token",
                ),
            ],
        )
        self.assertEqual(upstream.request_headers[0], upstream.request_headers[1])
        self.assertEqual(
            dict(upstream.request_headers[0]).get("Venus-Codex-Routing"),
            "true",
        )

    def test_retry_budget_exhaustion_returns_last_original_error(self) -> None:
        retryable = b'{"error":{"code":"invalid_encrypted_content"}}'
        request_body = b'{"opaque":"request"}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream(
                [(400, retryable), (400, retryable), (400, retryable)]
            ) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    backoffs=(0, 0),
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=request_body,
                        headers={"Authorization": "Bearer never-log-this"},
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()
            audit = audit_path.read_text(encoding="utf-8")

        self.assertEqual((response.status, response_body), (400, retryable))
        self.assertEqual(len(upstream.requests), 3)
        self.assertNotIn("never-log-this", audit)
        self.assertNotIn(request_body.decode(), audit)
        self.assertEqual(
            [json.loads(line)["attempt"] for line in audit.splitlines()],
            [1, 2, 3],
        )

    def test_upstream_timeout_returns_502_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(200, b'{"late":true}')], delay=0.1) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(
                    upstream.url,
                    audit_path,
                    upstream_timeout=0.01,
                ) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=b"{}",
                    )
                    response = connection.getresponse()
                    response_body = json.loads(response.read())
                    connection.close()

        self.assertEqual(response.status, 502)
        self.assertEqual(response_body["error"]["code"], "upstream_transport_error")
        self.assertEqual(len(upstream.requests), 1)

    def test_other_400_is_returned_without_retry(self) -> None:
        incompatible = b'{"error":{"code":"empty_string","param":"input[0]"}}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(400, incompatible)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(upstream.url, audit_path) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=b"{}",
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()
            audit_record = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertEqual((response.status, response_body), (400, incompatible))
        self.assertEqual(len(upstream.requests), 1)
        self.assertEqual(
            audit_record,
            {"attempt": 1, "status": 400, "error_code": "empty_string"},
        )

    def test_matching_error_code_on_non_400_is_not_retried(self) -> None:
        server_error = b'{"error":{"code":"invalid_encrypted_content"}}'

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with ScriptedUpstream([(500, server_error)]) as upstream:
                from scripts.pilot.venus_retry_proxy import RetryProxy

                with RetryProxy(upstream.url, audit_path) as proxy:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        proxy.port,
                        timeout=2,
                    )
                    connection.request("POST", "/v1/responses", body=b"{}")
                    response = connection.getresponse()
                    response_body = response.read()
                    connection.close()

        self.assertEqual((response.status, response_body), (500, server_error))
        self.assertEqual(len(upstream.requests), 1)

    def test_retry_policy_rejects_more_than_two_extra_attempts(self) -> None:
        from scripts.pilot.venus_retry_proxy import RetryProxy

        with tempfile.TemporaryDirectory() as temp:
            audit_path = Path(temp) / "venus-retry.jsonl"
            with self.assertRaisesRegex(ValueError, "at most two"):
                RetryProxy(
                    "http://127.0.0.1:1/llmproxy/v1",
                    audit_path,
                    backoffs=(0, 0, 0),
                )


if __name__ == "__main__":
    unittest.main()
