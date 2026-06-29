"""Port of viewerServerInfo.buildViewerServerInfo (the /__cad/server payload).

The launcher's reuse gate reads this strictly: serverApiVersion must be the
integer 2, dynamicRoot the boolean true, and serverFeatures must contain
"directory-activation". Optional serverMode/git keys are omitted when empty.
"""

from __future__ import annotations

import os

from . import scanner

VIEWER_SERVER_INFO_SCHEMA_VERSION = 1
VIEWER_SERVER_API_VERSION = 2
VIEWER_SERVER_APP_ID = "cad-viewer"
DEFAULT_VIEWER_HOST = "127.0.0.1"
DEFAULT_VIEWER_PORT = 4178


def normalize_viewer_port(value, fallback=DEFAULT_VIEWER_PORT) -> int:
    try:
        parsed = int(str(value if value is not None else ""))
    except (TypeError, ValueError):
        return fallback
    return parsed if 0 < parsed <= 65535 else fallback


def _resolve_view_root(directory_root: str, root_dir: str) -> dict:
    raw_root_dir = str(root_dir or "").strip()
    if not raw_root_dir:
        return {"dir": scanner.DEFAULT_VIEWER_ROOT_DIR, "rootPath": "", "rootName": ""}
    if os.path.isabs(raw_root_dir):
        resolved = os.path.abspath(raw_root_dir)
        return {"dir": resolved, "rootPath": resolved, "rootName": os.path.basename(resolved)}
    return scanner.resolve_viewer_root(directory_root, scanner.normalize_viewer_root_dir(raw_root_dir))


def build_viewer_server_info(
    *,
    directory_root: str,
    root_dir: str = scanner.DEFAULT_VIEWER_ROOT_DIR,
    port: int = DEFAULT_VIEWER_PORT,
    pid: int | None = None,
    host: str = DEFAULT_VIEWER_HOST,
    backend: str = "local-fs",
    dynamic_root: bool = False,
    step_artifact_generation_available: bool = True,
    viewer_version: str = "",
    server_mode: str = "",
    git: str = "",
    server_features=None,
    active_directories=None,
) -> dict:
    if not directory_root:
        raise ValueError("directoryRoot is required")
    resolved_directory_root = os.path.abspath(directory_root)
    view_root = _resolve_view_root(resolved_directory_root, root_dir)
    normalized_port = normalize_viewer_port(port)
    normalized_mode = str(server_mode or "").strip()
    normalized_git = str(git or "").strip()
    info = {
        "schemaVersion": VIEWER_SERVER_INFO_SCHEMA_VERSION,
        "serverApiVersion": VIEWER_SERVER_API_VERSION,
        "app": VIEWER_SERVER_APP_ID,
        "viewerVersion": str(viewer_version or ""),
    }
    if normalized_mode:
        info["serverMode"] = normalized_mode
    if normalized_git:
        info["git"] = normalized_git
    info["serverFeatures"] = [str(f or "").strip() for f in (server_features or []) if str(f or "").strip()]
    info["backend"] = backend
    info["dynamicRoot"] = bool(dynamic_root)
    info["directoryRoot"] = resolved_directory_root
    info["rootDir"] = view_root["dir"]
    info["rootPath"] = view_root["rootPath"]
    info["rootName"] = view_root["rootName"]
    info["activeDirectories"] = list(active_directories or [])
    info["port"] = normalized_port
    info["pid"] = pid if isinstance(pid, int) else os.getpid()
    info["stepArtifactGenerationAvailable"] = step_artifact_generation_available is not False
    info["url"] = f"http://{host}:{normalized_port}"
    return info
