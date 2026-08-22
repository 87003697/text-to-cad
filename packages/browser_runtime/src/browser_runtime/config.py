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
# These identities name the closed HTTP Render Programs baked into one exact image.
CAD_RENDER_PROGRAMS: Mapping[str, str] = {
    "residual": "sha256:3a4e126eac548fb0c2e134a1161e01085c95ce630d31c596d7329721cc6a3d9d",
    "snapshot": "sha256:9f7c44c619fe224688a838229b343709dd3be4364f01c9138f381ae403044b85",
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
