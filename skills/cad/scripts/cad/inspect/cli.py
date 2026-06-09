from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(scripts_dir))

from cad.inspect.inspect_refs.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
