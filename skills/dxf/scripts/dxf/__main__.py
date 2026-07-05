from __future__ import annotations

import os
import sys
from pathlib import Path

# Drawing packages are content-addressed (drawing.json dxfHash) and must be
# byte-deterministic. ezdxf's object-section creation order depends on Python
# hash randomization, so pin the seed and re-exec once before any ezdxf import.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

if __package__ in {None, ""}:
    tool_dir = Path(__file__).resolve().parent
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    from cli import main
else:
    from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
