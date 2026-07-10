"""Single-port CAD Viewer launcher (serve mode, Python backend).

Runs the `start` npm script: it starts the Python CAD Viewer backend — which
serves the prebuilt Vite bundle in `dist/` plus the /__cad API — on a single
port (default 3245). If the port is free it starts; if the port is already in
use it exits 1 with a `--port <n>` hint. It does NOT probe-and-reuse a running
Viewer or roll onto another port. Prints the load-bearing stdout contract (the
CAD Viewer URL line + optional --json {url,port,action}).

This is the consumer entry point for running the built Viewer. For local client
iteration in a source checkout use `npm run dev` (Vite/HMR) instead; see the
repo AGENTS.md for the dev-vs-prod and per-worktree port guidance.

Run: python -m server_py.start_viewer --dir <abs> [--port N] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server_py import cadgen_bridge
    from server_py.encoding import url_search_params_encode
    from server_py.server_info import DEFAULT_VIEWER_PORT, DEFAULT_VIEWER_HOST
else:
    from . import cadgen_bridge
    from .encoding import url_search_params_encode
    from .server_info import DEFAULT_VIEWER_PORT, DEFAULT_VIEWER_HOST

_PROBE_TIMEOUT_S = 0.35
_VIEWER_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def viewer_url(host: str, port: int, directory: str) -> str:
    return f"http://{host}:{port}/?{url_search_params_encode([('dir', os.path.abspath(directory))])}"


def port_is_free(host: str, port: int) -> bool:
    """True only when nothing is listening on host:port (connection refused). A
    live listener — or an ambiguous/unreachable socket — counts as occupied, so
    we never race a bind against another process."""
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def spawn_backend(host: str, port: int, directory: str):
    """Spawn the Python backend (serves the built dist + /__cad) on host:port."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_VIEWER_APP_ROOT, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["VIEWER_CAD_BACKEND_VALIDATED"] = "1"
    cmd = [sys.executable, "-m", "server_py.server", "--host", host, "--port", str(port), "--dir", os.path.abspath(directory)]
    return subprocess.Popen(cmd, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start the Python CAD Viewer backend on a single port")
    parser.add_argument("--host", default=DEFAULT_VIEWER_HOST)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_VIEWER_PORT)
    parser.add_argument("--json", action="store_true", dest="json_result")
    args, _unknown = parser.parse_known_args(argv)

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        print(f"--dir is not a directory: {directory}", file=sys.stderr)
        return 1

    host, port = args.host, args.port

    if not port_is_free(host, port):
        print(
            f"Port {port} on {host} is already in use. "
            f"Rerun with --port <n> to use a different port.",
            file=sys.stderr,
        )
        return 1

    try:
        cadgen_bridge.require_cadgen_runtime(directory)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    url = viewer_url(host, port, directory)
    print(f"Starting CAD Viewer at {url}")
    print(f"CAD Viewer URL: {url}")
    if args.json_result:
        print(json.dumps({"url": url, "port": port, "action": "start"}))
    sys.stdout.flush()
    child = spawn_backend(host, port, directory)
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
