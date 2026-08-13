"""Fixed isolated Linux handoff from the project interpreter to a sealed fd."""

from __future__ import annotations

import os
import sys


_SCHEMA = "meshshot.fd-exec-handoff/1"
_FAILURE = b"F"


def main() -> int:
    handshake_fd: int | None = None
    try:
        if len(sys.argv) < 4:
            raise OSError("invalid fd-native handoff")
        executable_fd = int(sys.argv[2])
        handshake_fd = int(sys.argv[3])
        browser_argv = sys.argv[4:]
        if (
            sys.argv[1] != _SCHEMA
            or executable_fd < 0
            or handshake_fd < 0
            or executable_fd == handshake_fd
            or not browser_argv
            or not os.path.isabs(browser_argv[0])
            or os.execve not in os.supports_fd
        ):
            raise OSError("fd-native execution is unavailable")
        os.set_inheritable(handshake_fd, False)
        os.execve(executable_fd, browser_argv, dict(os.environ))
    except (IndexError, OSError, TypeError, ValueError):
        if handshake_fd is not None:
            try:
                os.write(handshake_fd, _FAILURE)
            except OSError:
                pass
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
