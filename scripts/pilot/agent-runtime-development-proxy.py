#!/usr/bin/env python3
"""Start one Development/Not Sealed/Not Formal cup_cup_033 Venus proxy."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pilot.agent_runtime.development_venus_proxy import (
    CostPolicy,
    DevelopmentProxy,
    VENUS_BASE_URL,
)


def _secret(path: Path, label: str) -> str:
    try:
        payload = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{label} secret reference is unreadable") from error
    value = payload[:-1] if payload.endswith("\n") else payload
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} secret reference is invalid")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--total-ledger", type=Path)
    parser.add_argument("--client-token-file", type=Path, required=True)
    parser.add_argument("--upstream-token-file", type=Path, required=True)
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--bind-port", type=int, default=8080)
    parser.add_argument("--max-attempts", type=int, default=16)
    parser.add_argument("--max-request-bytes", type=int, default=200_000)
    parser.add_argument("--max-output-tokens", type=int, default=40_000)
    parser.add_argument("--upstream-timeout", type=float, default=180)
    parser.add_argument("--target", default=VENUS_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--provider-free-mock", action="store_true", help="allow a non-Venus mock target; zero paid dispatch only")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        policy = CostPolicy(
            max_attempts=args.max_attempts,
            max_request_bytes=args.max_request_bytes,
            max_output_tokens=args.max_output_tokens,
            per_job_usd=Decimal("100"),
            total_usd=Decimal("1000"),
        )
        client_token = _secret(args.client_token_file, "client")
        upstream_token = _secret(args.upstream_token_file, "upstream")
        proxy = DevelopmentProxy(
            args.target,
            args.ledger,
            total_ledger_path=args.total_ledger,
            upstream_token=upstream_token,
            client_token=client_token,
            job_id=args.job_id,
            policy=policy,
            upstream_timeout=args.upstream_timeout,
            bind_host=args.bind_host,
            bind_port=args.bind_port,
            allow_mock_target=args.provider_free_mock,
        )
    except (OSError, ValueError) as error:
        sys.stderr.write(f"Development proxy admission failed: {error}\n")
        return 2
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    proxy.start()
    # Body/header/token-free readiness, safe for a container healthcheck.
    sys.stdout.write(json.dumps({
        "classification": "Development/Not Sealed/Not Formal",
        "health": f"http://{args.bind_host}:{proxy.port}/healthz",
        "jobId": args.job_id,
        "worstCaseJobUsd": f"{policy.worst_case_job_usd:.6f}",
    }, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    stop.wait()
    proxy.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
