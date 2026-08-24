"""One-object stdin/stdout adapter for the Agent Surface handler."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from handler import (
    MAX_REQUEST_BYTES,
    AgentSurface,
    AgentSurfaceError,
    error_document,
)


def _emit(stream: TextIO, value: dict) -> None:
    stream.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def main(
    ports=None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Read exactly one JSON intent and emit exactly one JSON response.

    W4 supplies ``ports`` when wiring a real supervisor.  The standalone
    entrypoint deliberately has no authority/path discovery fallback.
    """

    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    try:
        raw = input_stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise AgentSurfaceError("request_too_large", "$.request")
        request = json.loads(raw)
        response = AgentSurface(ports).handle(request)
    except AgentSurfaceError as error:
        _emit(output_stream, {"ok": False, **error_document(error)})
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        error = AgentSurfaceError("invalid_request", "$.request")
        _emit(output_stream, {"ok": False, **error_document(error)})
        return 2
    _emit(output_stream, {"ok": True, "response": response})
    return 0


__all__ = ["main"]
