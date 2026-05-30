from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cadpy_common.package_path import ensure_cadpy_package_path

ensure_cadpy_package_path()

from cadpy.interference import main as cadpy_interference_main


def main(argv: Sequence[str] | None = None) -> int:
    return cadpy_interference_main(argv)
