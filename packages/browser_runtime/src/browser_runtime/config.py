"""Path and contract constants shared between the outer runner and tests.

The mount root name is inherited from the previous sidecar contract so that
the sandbox-visible path stays stable across the migration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

SANDBOX_MOUNT_ROOT = "/run/meshshot-browser"
SANDBOX_CODEX_CONFIG_NAME = "codex-config.toml"
SANDBOX_CODEX_CONFIG_PATH = f"{SANDBOX_MOUNT_ROOT}/{SANDBOX_CODEX_CONFIG_NAME}"
SANDBOX_RUNTIME_CAPABILITY_NAME = "runtime.json"
SANDBOX_RUNTIME_CAPABILITY_PATH = (
    f"{SANDBOX_MOUNT_ROOT}/{SANDBOX_RUNTIME_CAPABILITY_NAME}"
)
RUNTIME_CAPABILITY_SCHEMA = "text-to-cad.browser-runtime-capability/1"
# This identity names the closed HTTP residual-render operation.
CAD_RENDER_PROGRAMS: Mapping[str, str] = {
    "residual": "sha256:9d8e841ee6acef17e842e9ed23d8d537a155b5f7a36d4838758af4cb348d1359"
}

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
IMAGE_LOCK_PATH = _PACKAGE_ROOT / "image" / "image-lock.json"
HOST_IMAGE_LOCK_PATH = (
    Path.home() / ".local/state/text-to-cad/browser-runtime/image-lock.json"
)

BROWSER_RUNTIME_CONTRACT: Mapping[str, str] = {
    "sandbox_mount_root": SANDBOX_MOUNT_ROOT,
    "sandbox_codex_config_name": SANDBOX_CODEX_CONFIG_NAME,
    "sandbox_codex_config_path": SANDBOX_CODEX_CONFIG_PATH,
    "sandbox_runtime_capability_name": SANDBOX_RUNTIME_CAPABILITY_NAME,
    "sandbox_runtime_capability_path": SANDBOX_RUNTIME_CAPABILITY_PATH,
}


def load_image_lock(path: Path | None = None) -> dict:
    target = path or IMAGE_LOCK_PATH
    with target.open("r", encoding="utf-8") as fh:
        return json.load(fh)
