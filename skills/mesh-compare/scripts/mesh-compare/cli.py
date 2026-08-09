"""Public mesh comparison commands."""

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
from meshscope.voxblame import (
    PrepareReferenceError,
    measure_step,
    prepare_reference,
    publish_prepare_failure,
    run_step,
)


def _measure_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-measure",
        description="Measure and atomically publish one canonical Measured Step.",
    )
    parser.add_argument(
        "candidate", type=Path, help="Canonical candidate mesh"
    )
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="Published Canonical Reference directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="VoxBlame measurement-state directory",
    )
    parser.add_argument(
        "--step", type=int, required=True, help="Measured Step number"
    )
    parser.add_argument(
        "--compare-to",
        type=int,
        help="Explicit earlier Measured Step for a nonzero step",
    )
    args = parser.parse_args(argv)
    try:
        result = measure_step(
            args.reference,
            args.candidate,
            args.output,
            step=args.step,
            compare_to=args.compare_to,
        )
    except Exception as exc:
        detail = str(exc)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "classification": "measurement_failed",
                        "detail": detail,
                    },
                },
                separators=(",", ":"),
            )
        )
        print(f"measurement_failed: {detail}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "idempotent": result.idempotent,
                "measurement": result.summary,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _prepare_reference_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-prepare-reference",
        description="Prepare and atomically publish a Canonical Reference.",
    )
    parser.add_argument("source", help="Raw input scene or mesh")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Canonical input publication directory",
    )
    args = parser.parse_args(argv)
    try:
        result = prepare_reference(args.source, args.output)
    except PrepareReferenceError as error:
        failure_path = publish_prepare_failure(
            source=args.source,
            output=args.output,
            error=error,
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "classification": error.classification,
                        "phase": error.phase,
                        "detail": error.detail,
                    },
                    "failure_evidence": str(failure_path),
                },
                separators=(",", ":"),
            )
        )
        print(f"{error.classification}: {error.detail}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "idempotent": result.idempotent,
                "canonical_reference": result.manifest,
            },
            separators=(",", ":"),
        )
    )
    return 0


def main(argv=None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if argv and argv[0] == "voxblame-measure":
        return _measure_main(argv[1:])
    if argv and argv[0] == "voxblame-prepare-reference":
        return _prepare_reference_main(argv[1:])
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
    parser.add_argument(
        "--voxblame-dir",
        help="Opt in to sparse surface grading and persist state in this directory",
    )
    parser.add_argument("--step", type=int, help="Immutable VoxBlame candidate snapshot number")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="VoxBlame Morton surface depth (default: 8)",
    )
    parser.add_argument(
        "--compare-to",
        type=int,
        help="Earlier published VoxBlame step to compare against",
    )
    args = parser.parse_args(argv)

    if (args.voxblame_dir is None) != (args.step is None):
        parser.error("--voxblame-dir and --step must be provided together")
    if args.compare_to is not None and args.voxblame_dir is None:
        parser.error("--compare-to requires --voxblame-dir and --step")

    try:
        pair = prepare(args.mesh_a, args.mesh_b)
        result = compare(
            pair,
            n_samples=args.samples,
            include_distances=args.include_distances,
            seed=args.seed,
        )
        voxblame = None
        if args.voxblame_dir is not None:
            # The opt-in contract names mesh_a as reference and mesh_b as
            # candidate. Legacy numeric metrics remain symmetric and unchanged.
            voxblame = run_step(
                args.mesh_a,
                args.mesh_b,
                args.voxblame_dir,
                args.step,
                max_depth=args.max_depth,
                compare_to=args.compare_to,
            )
    except Exception as exc:
        payload = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(payload, indent=None if args.quiet else 2))
        return 2

    payload = {"ok": True, **result}
    if voxblame is not None:
        payload["voxblame"] = voxblame
    print(json.dumps(payload, indent=None if args.quiet else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
