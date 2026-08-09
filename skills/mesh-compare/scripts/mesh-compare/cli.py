"""Public mesh comparison commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


_BUNDLED_MESHSCOPE = (
    Path(__file__).resolve().parents[1] / "packages" / "meshscope" / "src"
)
_BUNDLED_MESHSHOT = (
    Path(__file__).resolve().parents[1] / "packages" / "meshshot" / "src"
)
for _runtime_source in (_BUNDLED_MESHSHOT, _BUNDLED_MESHSCOPE):
    if _runtime_source.is_dir():
        sys.path.insert(0, str(_runtime_source))

from meshscope.compare import compare, prepare
from meshscope.voxblame import (
    PrepareReferenceError,
    measure_step,
    page_repair_targets,
    prepare_preview_scene,
    prepare_reference,
    publish_preview,
    publish_region_diff,
    publish_prepare_failure,
    run_step,
    validate_preview_identity,
)
from meshshot import MeshGeometry, load_profile, render_residual_preview


def _emit_error(classification: str, detail: str) -> int:
    """Write one stable public error envelope."""

    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "classification": classification,
                    "detail": detail,
                },
            },
            separators=(",", ":"),
        )
    )
    print(f"{classification}: {detail}", file=sys.stderr)
    return 2


def _compact_detail(value: object, limit: int = 1000) -> str:
    detail = " ".join(str(value).split())
    if len(detail) <= limit:
        return detail
    return detail[: limit - 3] + "..."


def _read_json_object(path: Path, label: str) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, raw


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
        return _emit_error("measurement_failed", str(exc))
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


def _targets_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-targets",
        description="Page the frozen Repair Targets of a Measured Step.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="VoxBlame measurement-state directory",
    )
    parser.add_argument("--step", type=int, required=True, help="Measured Step number")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Zero-based offset in the frozen target order (default: 0)",
    )
    args = parser.parse_args(argv)
    try:
        page = page_repair_targets(args.output, step=args.step, offset=args.offset)
    except Exception as exc:
        classification = getattr(exc, "classification", "target_page_failed")
        detail = getattr(exc, "detail", str(exc))
        path = getattr(exc, "path", None)
        if path:
            detail = f"{path}: {detail}"
        return _emit_error(classification, detail)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "step": args.step,
                "repair_targets": page,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _diff_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-diff",
        description="Publish objective Region Diff evidence for a Repair Batch.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="VoxBlame measurement-state directory",
    )
    parser.add_argument("--from-step", type=int, required=True)
    parser.add_argument("--to-step", type=int, required=True)
    parser.add_argument("--repair-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = publish_region_diff(
            args.workspace,
            from_step=args.from_step,
            to_step=args.to_step,
            repair_plan=args.repair_plan,
            output=args.output,
        )
    except Exception as exc:
        classification = getattr(exc, "classification", "region_diff_failed")
        detail = getattr(exc, "detail", str(exc))
        path = getattr(exc, "path", None)
        if path:
            detail = f"{path}: {detail}"
        return _emit_error(classification, detail)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "idempotent": result.idempotent,
                "region_diff": result.region_diff,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _preview_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-preview",
        description="Render and atomically publish one formal residual preview.",
    )
    parser.add_argument("candidate", type=Path, help="Canonical candidate mesh")
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
        help="Preview publication directory",
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        required=True,
        help="Experiment JSON freezing preview_profile name and SHA-256",
    )
    parser.add_argument(
        "--variant",
        choices=("step", "final"),
        default="step",
        help="Preview size/profile variant (default: step)",
    )
    parser.add_argument(
        "--selected-step",
        type=int,
        help="Selected Measured Step required by a final preview",
    )
    parser.add_argument(
        "--selected-summary",
        type=Path,
        help="Canonical voxblame.summary/1 for the selected final step",
    )
    args = parser.parse_args(argv)
    if args.variant == "final" and (
        args.selected_step is None
        or args.selected_step < 0
        or args.selected_summary is None
    ):
        return _emit_error(
            "preview_failed",
            "final preview requires a non-negative selected step and selected summary",
        )
    if args.variant == "step" and (
        args.selected_step is not None or args.selected_summary is not None
    ):
        return _emit_error(
            "preview_failed",
            "step preview must not declare a selected step",
        )
    try:
        experiment, _experiment_bytes = _read_json_object(
            args.experiment, "experiment"
        )
        experiment_profile = experiment.get("preview_profile")
        if not isinstance(experiment_profile, dict):
            raise ValueError("experiment preview_profile must be a JSON object")
        selected_summary = None
        selected_summary_sha256 = None
        if args.selected_summary is not None:
            selected_summary, selected_summary_bytes = _read_json_object(
                args.selected_summary, "selected summary"
            )
            selected_summary_sha256 = hashlib.sha256(
                selected_summary_bytes
            ).hexdigest()
        loaded_profile = load_profile()
        scene = prepare_preview_scene(args.reference, args.candidate)
        identity = validate_preview_identity(
            scene,
            profile_name=loaded_profile.profile["name"],
            profile_sha256=loaded_profile.sha256,
            experiment_profile=experiment_profile,
            variant=args.variant,
            selected_step=args.selected_step,
            selected_summary=selected_summary,
            selected_summary_sha256=selected_summary_sha256,
        )
        rendered = render_residual_preview(
            MeshGeometry(**scene.reference_geometry),
            MeshGeometry(**scene.candidate_geometry),
            variant=args.variant,
            exterior_directions=scene.exterior.exact["outside_directions"],
        )
        if (
            rendered.variant != args.variant
            or rendered.profile_sha256 != loaded_profile.sha256
        ):
            raise ValueError("renderer profile identity conflict")
        result = publish_preview(
            scene,
            png_bytes=rendered.png_bytes,
            output=args.output,
            profile=loaded_profile.profile,
            ordered_views=[dict(view) for view in rendered.views],
            identity=identity,
        )
    except Exception as exc:
        return _emit_error("preview_failed", _compact_detail(exc))
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "idempotent": result.idempotent,
                "preview": result.metadata,
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
    if argv and argv[0] == "voxblame-targets":
        return _targets_main(argv[1:])
    if argv and argv[0] == "voxblame-diff":
        return _diff_main(argv[1:])
    if argv and argv[0] == "voxblame-preview":
        return _preview_main(argv[1:])
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
