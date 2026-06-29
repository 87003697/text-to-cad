"""Port of viewerServerRegistry.mjs — the shared live-server registry.

The launcher reads this to find reusable servers; a running server writes itself
in on startup and removes itself on shutdown. Format and tmp path must match the
Node version exactly so a Node launcher discovers a Python server and vice versa.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

VIEWER_SERVER_REGISTRY_VERSION = 1
VIEWER_SERVER_REGISTRY_FILENAME = "cad-viewer-servers.json"
VIEWER_SERVER_APP_ID = "cad-viewer"


def registry_path(env=None) -> str:
    env = env if env is not None else os.environ
    configured = str(env.get("VIEWER_SERVER_REGISTRY") or "").strip()
    if configured:
        return os.path.abspath(configured)
    return os.path.join(tempfile.gettempdir(), VIEWER_SERVER_REGISTRY_FILENAME)


def process_is_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def is_viewer_server_info(value) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("app") == VIEWER_SERVER_APP_ID
        and isinstance(value.get("rootPath"), str)
        and isinstance(value.get("port"), int)
    )


def _normalize_servers(payload):
    source = payload if isinstance(payload, list) else (payload.get("servers") if isinstance(payload, dict) else [])
    normalized = []
    for server in source or []:
        if not isinstance(server, dict):
            continue
        entry = dict(server)
        try:
            entry["port"] = int(server.get("port"))
            entry["pid"] = int(server.get("pid"))
        except (TypeError, ValueError):
            continue
        if is_viewer_server_info(entry):
            normalized.append(entry)
    normalized.sort(key=lambda s: s["port"])
    return normalized


def read_registry(path=None, include_dead=False):
    path = path or registry_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    servers = _normalize_servers(payload)
    return [s for s in servers if include_dead or process_is_alive(s["pid"])]


def _write_servers(servers, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"version": VIEWER_SERVER_REGISTRY_VERSION, "servers": servers}
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def write_registry(server_info, path=None) -> bool:
    if not is_viewer_server_info(server_info):
        return False
    path = path or registry_path()
    try:
        current = read_registry(path)
        nxt = [s for s in current if s["port"] != server_info["port"] and s["pid"] != server_info["pid"]]
        nxt.append({**server_info, "registeredAt": datetime.now(timezone.utc).isoformat()})
        nxt.sort(key=lambda s: s["port"])
        _write_servers(nxt, path)
        return True
    except OSError:
        return False


def remove_registry_entry(server_info, path=None) -> bool:
    if not isinstance(server_info, dict):
        return False
    path = path or registry_path()
    try:
        current = read_registry(path, include_dead=True)
        nxt = [s for s in current if not (s["port"] == server_info.get("port") or s["pid"] == server_info.get("pid"))]
        _write_servers(nxt, path)
        return True
    except OSError:
        return False
