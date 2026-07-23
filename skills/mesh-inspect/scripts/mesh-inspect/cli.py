import argparse
import json

from meshscope import inspect


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a mesh file.")
    parser.add_argument("input", help="Path to mesh file (GLB/OBJ/STL/PLY)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = inspect(args.input)
    except Exception as e:
        payload = {"ok": False, "errors": [str(e)]}
        print(json.dumps(payload, indent=None if args.quiet else 2))
        return 2

    payload = {"ok": True, **result}
    print(json.dumps(payload, indent=None if args.quiet else 2))
    return 0
