"""Numeric mesh similarity CLI.

Prints a JSON object to stdout with chamfer / hausdorff / percentile stats.
See `skills/mesh-compare/references/compare-metrics.md` for the schema
and threshold interpretation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_BUNDLED_MESHSCOPE = (
    Path(__file__).resolve().parents[1] / "packages" / "meshscope" / "src"
)
if _BUNDLED_MESHSCOPE.is_dir():
    sys.path.insert(0, str(_BUNDLED_MESHSCOPE))

from meshscope.compare import compare, prepare


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compute similarity metrics between two mesh files.")
    parser.add_argument("mesh_a", help="Path to first mesh (source / generated)")
    parser.add_argument("mesh_b", help="Path to second mesh (target / reference)")
    parser.add_argument("--samples", type=int, default=50000, help="Point-sample count per mesh (default: 50000)")
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Deterministic surface-sampling seed (default: 0)",
    )
    parser.add_argument(
        "--include-distances",
        action="store_true",
        help="Append raw per-sample distances_a2b/distances_b2a arrays",
    )
    parser.add_argument("--quiet", action="store_true", help="Emit compact JSON (single line)")
    args = parser.parse_args(argv)

    try:
        pair = prepare(args.mesh_a, args.mesh_b)
        result = compare(
            pair,
            n_samples=args.samples,
            include_distances=args.include_distances,
            seed=args.seed,
        )
    except Exception as exc:
        payload = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(payload, indent=None if args.quiet else 2))
        return 2

    payload = {"ok": True, **result}
    print(json.dumps(payload, indent=None if args.quiet else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
