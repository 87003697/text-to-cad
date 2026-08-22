from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for _runtime_path in (
    SCRIPTS_DIR / "packages",
    SCRIPTS_DIR / "packages" / "cadgen" / "src",
):
    _runtime_path_text = str(_runtime_path)
    if _runtime_path.is_dir():
        while _runtime_path_text in sys.path:
            sys.path.remove(_runtime_path_text)
        sys.path.insert(0, _runtime_path_text)

from cadgen.canonical_build import main


if __name__ == "__main__":
    raise SystemExit(main())
