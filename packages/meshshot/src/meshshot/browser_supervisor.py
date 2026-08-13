"""Fixed outer browser supervisor for the provider-free preview sandbox."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[1]))

from meshshot.browser_runtime import (
    BrowserRuntimeError,
    PrelaunchedCdpRuntime,
    SUPERVISOR_OUTER_ROOT,
    SUPERVISOR_OUTER_SOCKET,
    SUPERVISOR_PROTOCOL_SCHEMA,
    _receive_supervisor_packet,
    _send_supervisor_packet,
)


def _validate_root() -> None:
    try:
        info = SUPERVISOR_OUTER_ROOT.lstat()
    except OSError as exc:
        raise BrowserRuntimeError("browser_profile") from exc
    if (
        not SUPERVISOR_OUTER_ROOT.is_absolute()
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise BrowserRuntimeError("browser_profile")


def _validate_message(value: Any, expected: dict[str, str]) -> None:
    if not isinstance(value, dict) or value != expected:
        raise BrowserRuntimeError("browser_connect")


def run() -> None:
    """Own exactly one browser and one one-shot authority exchange."""

    _validate_root()
    if os.path.lexists(SUPERVISOR_OUTER_SOCKET):
        raise BrowserRuntimeError("browser_profile")
    configured = os.environ.get("MESHSHOT_BROWSER_EXECUTABLE")
    if configured is None:
        raise BrowserRuntimeError("browser_identity")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    connection: socket.socket | None = None
    bound = False
    try:
        server.bind(os.fspath(SUPERVISOR_OUTER_SOCKET))
        bound = True
        os.chmod(SUPERVISOR_OUTER_SOCKET, 0o600)
        socket_info = SUPERVISOR_OUTER_SOCKET.lstat()
        if (
            stat.S_ISLNK(socket_info.st_mode)
            or not stat.S_ISSOCK(socket_info.st_mode)
            or socket_info.st_uid != os.geteuid()
            or stat.S_IMODE(socket_info.st_mode) != 0o600
        ):
            raise BrowserRuntimeError("browser_profile")
        try:
            os.chmod(SUPERVISOR_OUTER_ROOT, 0o500)
        except OSError as exc:
            raise BrowserRuntimeError("browser_profile") from exc
        server.listen(1)
        server.settimeout(15.0)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            runtime = PrelaunchedCdpRuntime(Path(configured))
            with runtime.open(playwright.chromium):
                try:
                    connection, _address = server.accept()
                except (OSError, socket.timeout) as exc:
                    raise BrowserRuntimeError("browser_connect") from exc
                connection.settimeout(15.0)
                _validate_message(
                    _receive_supervisor_packet(connection),
                    {"schema": SUPERVISOR_PROTOCOL_SCHEMA, "type": "hello"},
                )
                _send_supervisor_packet(connection, runtime.supervisor_authority())
                completion = _receive_supervisor_packet(connection)
                if completion not in (
                    {
                        "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                        "type": "completion",
                        "result": "passed",
                    },
                    {
                        "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                        "type": "completion",
                        "result": "failed",
                    },
                ):
                    raise BrowserRuntimeError("browser_connect")
                _send_supervisor_packet(
                    connection,
                    {"schema": SUPERVISOR_PROTOCOL_SCHEMA, "type": "shutdown"},
                )
    finally:
        failure = False
        if connection is not None:
            try:
                connection.close()
            except OSError:
                failure = True
        try:
            server.close()
        except OSError:
            failure = True
        if bound:
            try:
                os.chmod(SUPERVISOR_OUTER_ROOT, 0o700)
                SUPERVISOR_OUTER_SOCKET.unlink()
            except OSError:
                failure = True
        if os.path.lexists(SUPERVISOR_OUTER_SOCKET):
            failure = True
        if failure:
            raise BrowserRuntimeError("browser_cleanup")


def main() -> int:
    try:
        run()
    except (BrowserRuntimeError, OSError, RuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
