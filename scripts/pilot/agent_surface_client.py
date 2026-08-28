#!/usr/bin/env python3
"""Fixed-path client used inside the authority-hidden Agent sandbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys


SOCKET_PATH = Path("/run/mesh-to-cad-agent-surface.sock")
MAX_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024


def _socket() -> socket.socket:
    path = Path(os.environ.get("MESH_TO_CAD_AGENT_SURFACE_SOCKET", SOCKET_PATH))
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(os.fspath(path))
    return connection


def _send(stream, request: object) -> dict:
    payload = json.dumps(
        request, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError("request_too_large")
    stream.write(payload + b"\n")
    stream.flush()
    response = stream.readline(MAX_RESPONSE_BYTES + 1)
    if not response or len(response) > MAX_RESPONSE_BYTES:
        raise ValueError("invalid_response")
    value = json.loads(response)
    if not isinstance(value, dict):
        raise ValueError("invalid_response")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mesh-to-cad-agent-surface")
    parser.add_argument("--mcp", action="store_true")
    args = parser.parse_args(argv)
    try:
        with _socket() as connection:
            stream = connection.makefile("rwb")
            if args.mcp:
                for line in sys.stdin.buffer:
                    try:
                        request = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        response = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "invalid JSON"},
                        }
                        sys.stdout.write(
                            json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                            + "\n"
                        )
                        sys.stdout.flush()
                        continue
                    response = _send(stream, request)
                    if response.get("__notification__"):
                        continue
                    sys.stdout.write(
                        json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                        + "\n"
                    )
                    sys.stdout.flush()
            else:
                try:
                    request = json.loads(sys.stdin.read())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response = {
                        "ok": False,
                        "error": {
                            "schema": "mesh-to-cad.agent-error/1",
                            "error": {
                                "classification": "invalid_request",
                                "path": "$.request",
                                "detail": "invalid_request",
                            },
                        },
                    }
                    sys.stdout.write(
                        json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                        + "\n"
                    )
                    sys.stdout.flush()
                    return 2
                response = _send(stream, request)
                sys.stdout.write(
                    json.dumps(response, ensure_ascii=True, separators=(",", ":"))
                    + "\n"
                )
                sys.stdout.flush()
                if response.get("ok") is False:
                    return 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"agent-surface: {type(error).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
