#!/usr/bin/env python3
"""Run one fixed acbafef8 cup_cup_033 Development container."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pilot.agent_runtime.canonical_json import canonical_json_bytes
from scripts.pilot.agent_runtime.development_supervisor import (
    DockerEngine,
    MAX_TIMEOUT_SECONDS,
    REPO_ROOT,
    SupervisorError,
    execute,
    fixed_candidate_request,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-job Development/Not Sealed/Not Formal Cup supervisor"
    )
    parser.add_argument("--image-id", required=True, help="exact imported Docker image ID")
    parser.add_argument("--source-dir", type=Path, required=True, help="host-visible fixed Cup source directory")
    parser.add_argument("--input-dir", type=Path, required=True, help="host-visible fixed Cup input directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="fresh host-visible result directory")
    parser.add_argument("--workload", type=Path, required=True, help="canonical JSON absolute argv array")
    parser.add_argument(
        "--broker-parent", type=Path, required=True,
        help="short existing /Users directory shared with Colima for the job-private Unix socket",
    )
    parser.add_argument("--timeout-seconds", type=int, default=MAX_TIMEOUT_SECONDS)
    parser.add_argument(
        "--internal-network",
        help="pre-created SAI-010 job-private internal network; omitted means network none",
    )
    parser.add_argument("--proxy-base-url", help="job-private internal proxy base ending in /v1")
    parser.add_argument(
        "--proxy-client-token-file", type=Path,
        help="read-only reference containing the one-job Agent-to-Proxy capability",
    )
    parser.add_argument("--docker", default="docker", help="Docker-compatible CLI executable")
    parser.add_argument("--docker-context", help="explicit Docker context/profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        proxy_client_token = None
        if args.proxy_client_token_file is not None:
            proxy_client_token = args.proxy_client_token_file.read_text(encoding="utf-8").strip()
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        request = fixed_candidate_request(
            repo_root=REPO_ROOT,
            image_id=args.image_id,
            source_dir=args.source_dir,
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            workload_path=args.workload,
            broker_parent=args.broker_parent,
            timeout_seconds=args.timeout_seconds,
            internal_network=args.internal_network,
            proxy_base_url=args.proxy_base_url,
            proxy_client_token=proxy_client_token,
        )
        receipt = execute(
            request, engine=DockerEngine(args.docker, context=args.docker_context)
        )
    except (OSError, SupervisorError) as error:
        sys.stderr.write(f"Development supervisor failed: {error}\n")
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
