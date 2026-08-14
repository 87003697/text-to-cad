"""Fixed outer browser supervisor for the provider-free preview sandbox."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[1]))

from meshshot.browser_runtime import (
    BrowserRuntimeError,
    PrelaunchedCdpRuntime,
    SUPERVISOR_AUTHORITY_SCHEMA,
    SUPERVISOR_CLIENT_SCHEMA,
    SUPERVISOR_OUTER_AUTHORITY,
    SUPERVISOR_OUTER_CLIENT,
    SUPERVISOR_OUTER_RESULT,
    SUPERVISOR_OUTER_ROOT,
    SUPERVISOR_OUTER_SOCKET,
    SUPERVISOR_PROTOCOL_SCHEMA,
    SUPERVISOR_RESULT_SCHEMA,
    _SUPERVISOR_PACKET_LIMIT,
    _loads_json_strict,
    _peer_credentials,
    _receive_supervisor_packet,
    _send_supervisor_packet,
)

_TIMEOUT_SECONDS = 15.0


class _SupervisorSignal(BaseException):
    pass


def _restore_inherited_runtime_signals() -> None:
    """Unblock only signals atomically blocked by the spawning parent."""

    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    if callable(pthread_sigmask):
        pthread_sigmask(
            signal.SIG_UNBLOCK,
            {signal.SIGINT, signal.SIGTERM},
        )


def _validate_root() -> tuple[int, tuple[int, int]]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            SUPERVISOR_OUTER_ROOT,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as cleanup_exc:
                raise BrowserRuntimeError("browser_cleanup") from cleanup_exc
        raise BrowserRuntimeError("browser_profile") from exc
    if (
        not SUPERVISOR_OUTER_ROOT.is_absolute()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        try:
            os.close(descriptor)
        except OSError:
            raise BrowserRuntimeError("browser_cleanup")
        raise BrowserRuntimeError("browser_profile")
    return descriptor, (info.st_dev, info.st_ino)


def _validate_message(value: Any, *, expected_type: str, nonce: str) -> str | None:
    expected = {"schema", "type", "nonce"}
    if expected_type == "completion":
        expected.add("result")
    if not isinstance(value, dict) or set(value) != expected:
        raise BrowserRuntimeError("browser_connect")
    if (
        value.get("schema") != SUPERVISOR_PROTOCOL_SCHEMA
        or value.get("type") != expected_type
        or value.get("nonce") != nonce
    ):
        raise BrowserRuntimeError("browser_connect")
    if expected_type == "completion":
        result = value.get("result")
        if result not in {"passed", "failed"}:
            raise BrowserRuntimeError("browser_connect")
        return result
    return None


def _write_private_record(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not raw or len(raw) > _SUPERVISOR_PACKET_LIMIT:
        raise BrowserRuntimeError("browser_profile")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        if os.write(descriptor, raw) != len(raw):
            raise OSError("short write")
        os.fsync(descriptor)
    except OSError as exc:
        raise BrowserRuntimeError("browser_profile") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise BrowserRuntimeError("browser_cleanup") from exc


def _load_expected_client(*, nonce: str, deadline: float) -> int:
    while time.monotonic() < deadline:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                SUPERVISOR_OUTER_CLIENT,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            info = os.fstat(descriptor)
            if info.st_size <= 0 or info.st_size > _SUPERVISOR_PACKET_LIMIT:
                raise OSError("invalid private client record size")
            raw = os.read(descriptor, _SUPERVISOR_PACKET_LIMIT + 1)
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        except OSError as exc:
            raise BrowserRuntimeError("browser_connect") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    raise BrowserRuntimeError("browser_cleanup") from exc
        value = _loads_json_strict(raw)
        client_pid = value.get("client_pid") if isinstance(value, dict) else None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or not isinstance(value, dict)
            or set(value) != {"schema", "client_pid", "nonce"}
            or value.get("schema") != SUPERVISOR_CLIENT_SCHEMA
            or value.get("nonce") != nonce
            or isinstance(client_pid, bool)
            or not isinstance(client_pid, int)
            or client_pid <= 1
        ):
            raise BrowserRuntimeError("browser_connect")
        return client_pid
    raise BrowserRuntimeError("browser_connect")


def _accept_authenticated_client(
    server: socket.socket,
    *,
    expected_pid: int,
    nonce: str,
    deadline: float,
) -> socket.socket:
    while time.monotonic() < deadline:
        server.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            connection, _address = server.accept()
        except (OSError, socket.timeout) as exc:
            raise BrowserRuntimeError("browser_connect") from exc
        accepted = False
        try:
            pid, uid, _gid = _peer_credentials(connection)
            if pid != expected_pid or uid != os.geteuid():
                continue
            _validate_message(
                _receive_supervisor_packet(connection),
                expected_type="hello",
                nonce=nonce,
            )
            accepted = True
            return connection
        except BrowserRuntimeError:
            continue
        finally:
            if not accepted:
                try:
                    connection.close()
                except OSError:
                    raise BrowserRuntimeError("browser_cleanup")
    raise BrowserRuntimeError("browser_connect")


def _unlink_owned_socket(root_fd: int, identity: tuple[int, int]) -> None:
    try:
        current = os.stat(
            SUPERVISOR_OUTER_SOCKET.name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise BrowserRuntimeError("browser_cleanup") from exc
    if (
        not stat.S_ISSOCK(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise BrowserRuntimeError("browser_cleanup")
    try:
        os.unlink(SUPERVISOR_OUTER_SOCKET.name, dir_fd=root_fd)
    except OSError as exc:
        raise BrowserRuntimeError("browser_cleanup") from exc
    try:
        os.stat(
            SUPERVISOR_OUTER_SOCKET.name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BrowserRuntimeError("browser_cleanup") from exc
    raise BrowserRuntimeError("browser_cleanup")


def _closed_result(exc: BrowserRuntimeError) -> dict[str, str]:
    value = {"schema": SUPERVISOR_RESULT_SCHEMA, "operation": exc.operation}
    if exc.browser_identity_substage is not None:
        value["browser_identity_substage"] = exc.browser_identity_substage
    if exc.browser_identity_phase is not None:
        value["browser_identity_phase"] = exc.browser_identity_phase
    if exc.browser_identity_check is not None:
        value["browser_identity_check"] = exc.browser_identity_check
    return value


def run() -> None:
    """Own exactly one browser and one authenticated, one-shot exchange."""

    root_fd, root_identity = _validate_root()
    server: socket.socket | None = None
    connection: socket.socket | None = None
    socket_identity: tuple[int, int] | None = None
    socket_unlinked = False
    nonce = os.urandom(32).hex()
    failure = False
    try:
        for path in (SUPERVISOR_OUTER_SOCKET, SUPERVISOR_OUTER_AUTHORITY, SUPERVISOR_OUTER_CLIENT):
            if os.path.lexists(path):
                raise BrowserRuntimeError("browser_profile")
        configured = os.environ.get("MESHSHOT_BROWSER_EXECUTABLE")
        if configured is None:
            raise BrowserRuntimeError("browser_identity")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(os.fspath(SUPERVISOR_OUTER_SOCKET))
        os.chmod(SUPERVISOR_OUTER_SOCKET, 0o600)
        socket_info = os.stat(
            SUPERVISOR_OUTER_SOCKET.name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISSOCK(socket_info.st_mode)
            or socket_info.st_uid != os.geteuid()
            or stat.S_IMODE(socket_info.st_mode) != 0o600
        ):
            raise BrowserRuntimeError("browser_profile")
        socket_identity = (socket_info.st_dev, socket_info.st_ino)
        server.listen(8)
        _write_private_record(
            SUPERVISOR_OUTER_AUTHORITY,
            {
                "schema": SUPERVISOR_AUTHORITY_SCHEMA,
                "supervisor_pid": os.getpid(),
                "nonce": nonce,
            },
        )
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            runtime = PrelaunchedCdpRuntime(Path(configured))
            with runtime.open(playwright.chromium):
                deadline = time.monotonic() + _TIMEOUT_SECONDS
                expected_pid = _load_expected_client(nonce=nonce, deadline=deadline)
                connection = _accept_authenticated_client(
                    server,
                    expected_pid=expected_pid,
                    nonce=nonce,
                    deadline=deadline,
                )
                server.close()
                server = None
                _unlink_owned_socket(root_fd, socket_identity)
                socket_unlinked = True
                _send_supervisor_packet(
                    connection,
                    {
                        **runtime.supervisor_authority(),
                        "nonce": nonce,
                    },
                )
                _validate_message(
                    _receive_supervisor_packet(connection),
                    expected_type="completion",
                    nonce=nonce,
                )
                _send_supervisor_packet(
                    connection,
                    {
                        "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                        "type": "shutdown",
                        "nonce": nonce,
                    },
                )
                connection.close()
                connection = None
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                failure = True
        try:
            if server is not None:
                server.close()
        except OSError:
            failure = True
        if socket_identity is not None and not socket_unlinked:
            try:
                _unlink_owned_socket(root_fd, socket_identity)
            except BrowserRuntimeError:
                failure = True
        try:
            current = os.fstat(root_fd)
            if (current.st_dev, current.st_ino) != root_identity:
                failure = True
        except OSError:
            failure = True
        try:
            os.chmod(SUPERVISOR_OUTER_ROOT, 0o700)
        except OSError:
            failure = True
        else:
            for path in (SUPERVISOR_OUTER_AUTHORITY, SUPERVISOR_OUTER_CLIENT):
                try:
                    os.unlink(path.name, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    failure = True
        try:
            os.close(root_fd)
        except OSError:
            failure = True
        if failure:
            raise BrowserRuntimeError("browser_cleanup")


def main() -> int:
    watched = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in watched}

    def terminate(_signum: int, _frame: object) -> None:
        raise _SupervisorSignal()

    try:
        for signum in watched:
            signal.signal(signum, terminate)
        _restore_inherited_runtime_signals()
        run()
    except _SupervisorSignal:
        return 0
    except BrowserRuntimeError as exc:
        try:
            if not os.path.lexists(SUPERVISOR_OUTER_RESULT):
                _write_private_record(SUPERVISOR_OUTER_RESULT, _closed_result(exc))
        except BrowserRuntimeError:
            pass
        return 1
    except (OSError, RuntimeError):
        return 1
    finally:
        for signum in watched:
            signal.signal(signum, previous[signum])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
