"""CAD skill command package."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PACKAGES_DIR = SCRIPTS_DIR / "packages"
CADPY_SRC_DIR = PACKAGES_DIR / "cadpy" / "src"

for runtime_path in (PACKAGES_DIR, CADPY_SRC_DIR):
    runtime_path_text = str(runtime_path)
    if runtime_path_text not in sys.path:
        sys.path.insert(0, runtime_path_text)
