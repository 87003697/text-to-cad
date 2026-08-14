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
    SUPERVISOR_RESULT_RECORD_CLEANUP_EXIT,
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
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_supervisor_state",
                    browser_cleanup_check="root_descriptor_close",
                ) from cleanup_exc
        raise BrowserRuntimeError("browser_profile") from exc
    if (
        not SUPERVISOR_OUTER_ROOT.is_absolute()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        try:
            os.close(descriptor)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_supervisor_state",
                browser_cleanup_check="root_descriptor_close",
            ) from exc
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
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage=(
                        "private_supervisor_record_descriptors"
                    ),
                    browser_cleanup_check=(
                        "authority_record_descriptor_close"
                        if path == SUPERVISOR_OUTER_AUTHORITY
                        else (
                            "result_record_descriptor_close"
                            if path == SUPERVISOR_OUTER_RESULT
                            else "client_record_descriptor_close"
                        )
                    ),
                ) from exc


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
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage=(
                            "private_supervisor_record_descriptors"
                        ),
                        browser_cleanup_check="client_record_descriptor_close",
                    ) from exc
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
                except OSError as exc:
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_supervisor_state",
                        browser_cleanup_check="client_transport_close",
                    ) from exc
    raise BrowserRuntimeError("browser_connect")


def _unlink_owned_socket(root_fd: int, identity: tuple[int, int]) -> None:
    try:
        current = os.stat(
            SUPERVISOR_OUTER_SOCKET.name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_supervisor_state",
            browser_cleanup_check="socket_unlink",
        ) from exc
    if (
        not stat.S_ISSOCK(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_supervisor_state",
            browser_cleanup_check="socket_unlink",
        )
    try:
        os.unlink(SUPERVISOR_OUTER_SOCKET.name, dir_fd=root_fd)
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_supervisor_state",
            browser_cleanup_check="socket_unlink",
        ) from exc
    try:
        os.stat(
            SUPERVISOR_OUTER_SOCKET.name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_supervisor_state",
            browser_cleanup_check="socket_unlink",
        ) from exc
    raise BrowserRuntimeError(
        "browser_cleanup",
        browser_cleanup_substage="private_supervisor_state",
        browser_cleanup_check="socket_unlink",
    )


def _closed_result(exc: BrowserRuntimeError) -> dict[str, str]:
    if (
        exc.operation == "browser_cleanup"
        and (
            exc.browser_cleanup_substage is None
            or exc.browser_cleanup_check is None
        )
    ):
        raise ValueError("untyped browser cleanup failure")
    value = {"schema": SUPERVISOR_RESULT_SCHEMA, "operation": exc.operation}
    if exc.browser_identity_substage is not None:
        value["browser_identity_substage"] = exc.browser_identity_substage
    if exc.browser_identity_phase is not None:
        value["browser_identity_phase"] = exc.browser_identity_phase
    if exc.browser_identity_check is not None:
        value["browser_identity_check"] = exc.browser_identity_check
    if exc.browser_cleanup_substage is not None:
        value["browser_cleanup_substage"] = exc.browser_cleanup_substage
    if exc.browser_cleanup_check is not None:
        value["browser_cleanup_check"] = exc.browser_cleanup_check
    return value


def _cleanup_private_supervisor_state(
    *,
    root_fd: int,
    root_identity: tuple[int, int],
    server: socket.socket | None,
    connection: socket.socket | None,
    socket_identity: tuple[int, int] | None,
    socket_unlinked: bool,
    initial_substage: str | None = None,
    initial_check: str | None = None,
) -> None:
    cleanup_substage = (
        initial_substage
        if initial_substage is not None and initial_check is not None
        else "private_supervisor_state"
    )
    cleanup_check = initial_check

    def record(check: str, *, retained: bool = False) -> None:
        nonlocal cleanup_substage, cleanup_check
        if cleanup_check is None or retained:
            cleanup_substage = "private_supervisor_state"
            cleanup_check = check

    if connection is not None:
        try:
            connection.close()
        except OSError:
            record("client_transport_close")
    if server is not None:
        try:
            server.close()
        except OSError:
            record("listener_close")
    if socket_identity is not None and not socket_unlinked:
        try:
            _unlink_owned_socket(root_fd, socket_identity)
        except BrowserRuntimeError:
            record("socket_unlink")
    try:
        current = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != root_identity:
            record("root_identity", retained=True)
    except OSError:
        record("root_identity")
    try:
        os.chmod(SUPERVISOR_OUTER_ROOT, 0o700)
    except OSError:
        record("root_identity")
    else:
        for path, check in (
            (SUPERVISOR_OUTER_AUTHORITY, "authority_record_unlink"),
            (SUPERVISOR_OUTER_CLIENT, "client_record_unlink"),
        ):
            try:
                os.unlink(path.name, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            except OSError:
                record(check)
            if os.path.lexists(path):
                record(check, retained=True)
    if os.path.lexists(SUPERVISOR_OUTER_SOCKET):
        record("socket_unlink", retained=True)
    try:
        os.close(root_fd)
    except OSError:
        record("root_descriptor_close")
    if cleanup_check is not None:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage=cleanup_substage,
            browser_cleanup_check=cleanup_check,
        )


def run() -> None:
    """Own exactly one browser and one authenticated, one-shot exchange."""

    root_fd, root_identity = _validate_root()
    server: socket.socket | None = None
    connection: socket.socket | None = None
    socket_identity: tuple[int, int] | None = None
    socket_unlinked = False
    nonce = os.urandom(32).hex()
    body_error: BaseException | None = None
    body_cleanup_substage: str | None = None
    body_cleanup_check: str | None = None
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
                try:
                    closing_server = server
                    server = None
                    closing_server.close()
                except OSError as exc:
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_supervisor_state",
                        browser_cleanup_check="listener_close",
                    ) from exc
                try:
                    unlink_identity = socket_identity
                    socket_identity = None
                    assert unlink_identity is not None
                    _unlink_owned_socket(root_fd, unlink_identity)
                except BrowserRuntimeError as exc:
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_supervisor_state",
                        browser_cleanup_check="socket_unlink",
                    ) from exc
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
                try:
                    closing_connection = connection
                    connection = None
                    closing_connection.close()
                except OSError as exc:
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_supervisor_state",
                        browser_cleanup_check="client_transport_close",
                    ) from exc
    except BaseException as exc:
        body_error = exc
        if (
            isinstance(exc, BrowserRuntimeError)
            and exc.operation == "browser_cleanup"
            and exc.browser_cleanup_substage is not None
            and exc.browser_cleanup_check is not None
        ):
            body_cleanup_substage = exc.browser_cleanup_substage
            body_cleanup_check = exc.browser_cleanup_check
    finally:
        try:
            _cleanup_private_supervisor_state(
                root_fd=root_fd,
                root_identity=root_identity,
                server=server,
                connection=connection,
                socket_identity=socket_identity,
                socket_unlinked=socket_unlinked,
                initial_substage=body_cleanup_substage,
                initial_check=body_cleanup_check,
            )
        except BrowserRuntimeError as cleanup_error:
            raise cleanup_error from body_error
    if body_error is not None:
        raise body_error


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
        except BrowserRuntimeError as cleanup_exc:
            if (
                cleanup_exc.browser_cleanup_substage
                == "private_supervisor_record_descriptors"
                and cleanup_exc.browser_cleanup_check
                == "result_record_descriptor_close"
            ):
                return SUPERVISOR_RESULT_RECORD_CLEANUP_EXIT
            raise cleanup_exc from exc
        except ValueError:
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
