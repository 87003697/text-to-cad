from __future__ import annotations

from pathlib import Path
import sys


CADPY_SRC = Path(__file__).resolve().parent.parent / "packages/cadpy/src"
sys.path.insert(0, str(CADPY_SRC))

from cadpy.canonical_build import main


if __name__ == "__main__":
    raise SystemExit(main())
