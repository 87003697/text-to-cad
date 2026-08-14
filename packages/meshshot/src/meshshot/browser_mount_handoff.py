"""Fixed detached read-only browser mount handoff for provider-free Linux."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import sys


SCHEMA = "meshshot.browser-mount-handoff/1"
AUTHORITY_SCHEMA = "meshshot.browser-mount-authority/1"
PACKET_LIMIT = 4096
SUPERVISOR_ROOT = Path("/run/meshshot-supervisor")
SOCKET_PATH = SUPERVISOR_ROOT / "browser-mount.sock"
AUTHORITY_PATH = SUPERVISOR_ROOT / "browser-mount-authority.json"
REVISION_ROOT = Path("/run/meshshot-browser/attested")
EXECUTABLE = REVISION_ROOT / (
    "chrome-headless-shell-linux64/chrome-headless-shell"
)
_PROFILE_NAME = "meshshot-cdp-"
_ENVIRONMENT = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHONDONTWRITEBYTECODE",
        "TMPDIR",
        "TZ",
    }
)


def _loads(raw: bytes) -> object:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate)


def _packet(value: dict[str, object]) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not raw or len(raw) > PACKET_LIMIT:
        raise OSError("invalid mount handoff packet")
    return raw


def _authority() -> str:
    descriptor = os.open(
        AUTHORITY_PATH,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        raw = os.read(descriptor, PACKET_LIMIT + 1)
    finally:
        os.close(descriptor)
    value = _loads(raw)
    nonce = value.get("nonce") if isinstance(value, dict) else None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o400
        or not isinstance(value, dict)
        or set(value) != {"schema", "nonce"}
        or value.get("schema") != AUTHORITY_SCHEMA
        or not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise OSError("invalid mount handoff authority")
    return nonce


def _live_argv(profile: str) -> list[str]:
    from meshshot.browser_runtime import _load_profile

    path = Path(profile)
    try:
        info = path.lstat()
    except OSError as exc:
        raise OSError("invalid browser profile") from exc
    if (
        path.parent != Path("/tmp")
        or not path.name.startswith(_PROFILE_NAME)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise OSError("invalid browser profile")
    profile_value, _profile_sha256 = _load_profile()
    return [
        os.fspath(EXECUTABLE),
        *profile_value["arguments"],
        f"--user-data-dir={path}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "about:blank",
    ]


def main() -> int:
    connection: socket.socket | None = None
    try:
        if len(sys.argv) not in {3, 4} or sys.argv[1] != SCHEMA:
            raise OSError("invalid mount handoff")
        mode = sys.argv[2]
        if mode == "version" and len(sys.argv) == 3:
            browser_argv = [os.fspath(EXECUTABLE), "--version"]
        elif mode == "live" and len(sys.argv) == 4:
            browser_argv = _live_argv(sys.argv[3])
        else:
            raise OSError("invalid mount handoff mode")
        executable_info = EXECUTABLE.lstat()
        filesystem = os.statvfs(REVISION_ROOT)
        if (
            stat.S_ISLNK(executable_info.st_mode)
            or not stat.S_ISREG(executable_info.st_mode)
            or executable_info.st_uid != os.geteuid()
            or executable_info.st_mode & 0o111 == 0
            or not filesystem.f_flag & getattr(os, "ST_RDONLY", 1)
        ):
            raise OSError("browser revision mount is not read-only")
        nonce = _authority()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        connection.connect(os.fspath(SOCKET_PATH))
        connection.sendall(
            _packet({"schema": SCHEMA, "type": "mounted", "nonce": nonce})
        )
        response = _loads(connection.recv(PACKET_LIMIT + 1))
        if (
            not isinstance(response, dict)
            or set(response) != {"schema", "type", "nonce"}
            or response.get("schema") != SCHEMA
            or response.get("type") != "detached"
            or response.get("nonce") != nonce
        ):
            raise OSError("browser source was not detached")
        connection.sendall(
            _packet({"schema": SCHEMA, "type": "exec", "nonce": nonce})
        )
        connection.close()
        connection = None
        environment = {
            name: os.environ[name]
            for name in sorted(_ENVIRONMENT)
            if name in os.environ
        }
        os.execve(EXECUTABLE, browser_argv, environment)
    except (OSError, TypeError, ValueError, UnicodeDecodeError):
        if connection is not None:
            try:
                connection.sendall(_packet({"schema": SCHEMA, "type": "failed"}))
            except OSError:
                pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
