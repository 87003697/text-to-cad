"""Fixed-port CAD Viewer launcher (serve-mode, Python backend).

Targets a single port (default 4178). Probes /__cad/server on it and either
reuses a compatible running viewer (activating the requested dir), starts the
backend when the port is free, or exits with an error when the port is held by
another process. It does NOT scan or roll onto other ports: rerun with an
explicit --port when 4178 is busy. Prints the load-bearing stdout contract (the
CAD Viewer URL line + optional --json {url,port,action}).

Run: python -m server_py.start_agent_viewer --dir <abs> [--port N] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server_py.encoding import url_search_params_encode
    from server_py.server_info import DEFAULT_VIEWER_PORT, DEFAULT_VIEWER_HOST
else:
    from .encoding import url_search_params_encode
    from .server_info import DEFAULT_VIEWER_PORT, DEFAULT_VIEWER_HOST

_PROBE_TIMEOUT_S = 0.35
_ACTIVATE_TIMEOUT_S = 30.0
_DIRECTORY_ACTIVATION_FEATURE = "directory-activation"
_VIEWER_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def agent_viewer_url(host: str, port: int, directory: str) -> str:
    return f"http://{host}:{port}/?{url_search_params_encode([('dir', os.path.abspath(directory))])}"


def is_reusable(server_info: dict) -> bool:
    if not isinstance(server_info, dict):
        return False
    features = server_info.get("serverFeatures") if isinstance(server_info.get("serverFeatures"), list) else []
    return bool(
        server_info.get("app") == "cad-viewer"
        and server_info.get("dynamicRoot") is True
        and _DIRECTORY_ACTIVATION_FEATURE in features
    )


def probe(host: str, port: int):
    """Return ('viewer', info) | ('closed', None) | ('occupied', None)."""
    url = f"http://{host}:{port}/__cad/server"
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S) as resp:
            if resp.status != 200:
                return ("occupied", None)
            info = json.loads(resp.read())
            return ("viewer", info) if isinstance(info, dict) and info.get("app") == "cad-viewer" else ("occupied", None)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
            return ("closed", None)
        return ("occupied", None)
    except (ConnectionRefusedError, OSError):
        return ("closed", None)
    except (ValueError, TimeoutError):
        return ("occupied", None)


def activate_directory(host: str, port: int, directory: str):
    from urllib.parse import quote
    url = f"http://{host}:{port}/__cad/directory/activate?dir={quote(os.path.abspath(directory))}"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        urllib.request.urlopen(req, timeout=_ACTIVATE_TIMEOUT_S).read()
    except (urllib.error.URLError, OSError, ValueError):
        pass


def select_mode() -> str:
    """dev (Vite serves the client + proxies /__cad to a spawned Python backend)
    when a source checkout with Vite is present; serve (Python serves the built
    dist + /__cad) for the bundled runtime. Overridable via VIEWER_AGENT_START_MODE."""
    requested = str(os.environ.get("VIEWER_AGENT_START_MODE") or "").strip()
    if requested in ("dev", "serve"):
        return requested
    has_vite = (
        os.path.isfile(os.path.join(_VIEWER_APP_ROOT, "vite.config.mjs"))
        and os.path.exists(os.path.join(_VIEWER_APP_ROOT, "node_modules", ".bin", "vite"))
    )
    return "dev" if has_vite else "serve"


def spawn_backend(mode: str, host: str, port: int, directory: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_VIEWER_APP_ROOT, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if mode == "dev":
        # Vite serves the client (HMR) and its config spawns the Python backend +
        # proxies /__cad to it. We forward the chosen host/port and the served dir.
        env["VIEWER_DEFAULT_DIR"] = os.path.abspath(directory)
        env.setdefault("VIEWER_AGENT_START_MODE", "dev")
        cmd = ["npm", "run", "dev", "--", "--host", host, "--port", str(port)]
        return subprocess.Popen(cmd, cwd=_VIEWER_APP_ROOT, env=env)
    cmd = [sys.executable, "-m", "server_py.server", "--host", host, "--port", str(port), "--dir", os.path.abspath(directory)]
    return subprocess.Popen(cmd, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start/reuse the Python CAD Viewer backend on a fixed port")
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
    status, info = probe(host, port)

    if status == "viewer" and is_reusable(info):
        activate_directory(host, port, directory)
        url = agent_viewer_url(host, port, directory)
        print(f"CAD Viewer already running at {url}")
        print(f"CAD Viewer URL: {url}")
        if args.json_result:
            print(json.dumps({"url": url, "port": port, "action": "reuse"}))
        return 0

    if status == "closed":
        mode = select_mode()
        url = agent_viewer_url(host, port, directory)
        print(f"Starting CAD Viewer {mode} server at {url}")
        print(f"CAD Viewer URL: {url}")
        if args.json_result:
            print(json.dumps({"url": url, "port": port, "action": "start"}))
        sys.stdout.flush()
        child = spawn_backend(mode, host, port, directory)
        return child.wait()

    # 'occupied', or a viewer that fails the reuse gate: do not roll to another port.
    print(
        f"Port {port} on {host} is already in use by another process. "
        f"Rerun with --port <n> to use a different port.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
