import argparse
import json
import sys
from pathlib import Path


_BUNDLED_MESHSCOPE = (
    Path(__file__).resolve().parents[1] / "packages" / "meshscope" / "src"
)
if _BUNDLED_MESHSCOPE.is_dir():
    sys.path.insert(0, str(_BUNDLED_MESHSCOPE))

from meshscope import inspect


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a mesh file.")
    parser.add_argument("input", help="Path to mesh file (GLB/OBJ/STL/PLY/3MF)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument(
        "--output",
        help="Write the JSON result to this path instead of stdout",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = inspect(args.input)
        payload = {"ok": True, **result}
        rendered = json.dumps(payload, indent=None if args.quiet else 2)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
    except Exception as e:
        payload = {"ok": False, "errors": [str(e)]}
        print(json.dumps(payload, indent=None if args.quiet else 2))
        return 2

    return 0
