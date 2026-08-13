"""Public mesh comparison commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PACKAGES_DIR = Path(__file__).resolve().parent.parent / "packages"
sys.path.insert(0, str(PACKAGES_DIR / "meshscope" / "src"))
sys.path.insert(0, str(PACKAGES_DIR / "meshshot" / "src"))

from meshscope.voxblame import (
    PrepareReferenceError,
    measure_step,
    page_repair_targets,
    prepare_preview_scene,
    prepare_reference,
    publish_preview,
    publish_region_diff,
    publish_prepare_failure,
    validate_preview_identity,
    verify_step,
)
from meshshot import MeshGeometry, MeshshotError, load_profile, render_residual_preview


_PREVIEW_FAILURE_CLASSIFICATIONS = {
    "runtime": "preview_runtime_failed",
    "dependency": "preview_dependency_failed",
    "browser_launch": "preview_browser_launch_failed",
    "browser_launch_process_limit": "preview_browser_launch_process_limit_failed",
    "browser_launch_file_limit": "preview_browser_launch_file_limit_failed",
    "browser_launch_address_space": "preview_browser_launch_address_space_failed",
    "browser_launch_shared_memory": "preview_browser_launch_shared_memory_failed",
    "browser_launch_executable": "preview_browser_launch_executable_failed",
    "browser_launch_executable_missing": (
        "preview_browser_launch_executable_missing_failed"
    ),
    "browser_launch_executable_permission": (
        "preview_browser_launch_executable_permission_failed"
    ),
    "browser_launch_executable_spawn_permission": (
        "preview_browser_launch_executable_spawn_permission_failed"
    ),
    "browser_launch_sandbox_permission": (
        "preview_browser_launch_sandbox_permission_failed"
    ),
    "browser_launch_filesystem_permission": (
        "preview_browser_launch_filesystem_permission_failed"
    ),
    "browser_launch_executable_dependency": (
        "preview_browser_launch_executable_dependency_failed"
    ),
    "browser_adapter_profile": "preview_browser_adapter_profile_failed",
    "browser_identity": "preview_browser_identity_failed",
    "browser_profile": "preview_browser_profile_failed",
    "browser_prelaunch": "preview_browser_prelaunch_failed",
    "browser_readiness": "preview_browser_readiness_failed",
    "browser_readiness_timeout": "preview_browser_readiness_timeout_failed",
    "browser_connect": "preview_browser_connect_failed",
    "browser_cleanup": "preview_browser_cleanup_failed",
    "browser_signal": "preview_browser_signal_failed",
    "browser_render": "preview_browser_render_failed",
    "browser_result": "preview_browser_result_failed",
}


def _emit_error(
    classification: str,
    detail: str,
    *,
    diagnostic: dict[str, str] | None = None,
) -> int:
    """Write one stable public error envelope."""

    error = {
        "classification": classification,
        "detail": detail,
    }
    if diagnostic is not None:
        error["diagnostic"] = diagnostic
    print(
        json.dumps(
            {
                "ok": False,
                "error": error,
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
                "backend": result.backend,
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


def _verify_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-compare voxblame-verify",
        description="Verify a rebuilt mesh without publishing a Measured Step.",
    )
    parser.add_argument("candidate", type=Path, help="Rebuilt canonical mesh")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--against-step", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_step(
            args.reference,
            args.candidate,
            args.workspace,
            against_step=args.against_step,
            output=args.output,
        )
    except Exception as exc:
        return _emit_error("verification_failed", _compact_detail(exc))
    payload = {
        "ok": result.verification["verified"],
        "output": str(args.output) if result.published else None,
        "verification": result.verification,
    }
    print(json.dumps(payload, separators=(",", ":")))
    if result.verification["verified"]:
        return 0
    print("verification_mismatch: rebuilt Observable Geometry differs", file=sys.stderr)
    return 2


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
            browser_runtime=dict(rendered.browser_runtime or {}),
            identity=identity,
        )
    except MeshshotError as exc:
        classification = _PREVIEW_FAILURE_CLASSIFICATIONS.get(
            exc.phase, "preview_failed"
        )
        diagnostic = None
        if (
            exc.phase == "browser_identity"
            and exc.browser_identity_substage is not None
            and (
                exc.browser_identity_substage
                != "private_snapshot_launch_image_identity"
                or exc.browser_identity_phase is not None
            )
        ):
            diagnostic = {
                "schema": "meshshot.browser-identity-failure/6",
                "substage": exc.browser_identity_substage,
            }
            if (
                exc.browser_identity_substage
                == "private_snapshot_launch_image_identity"
                and exc.browser_identity_phase is not None
            ):
                diagnostic["phase"] = exc.browser_identity_phase
                if (
                    exc.browser_identity_phase
                    in {
                        "playwright_package_revision_identity",
                        "private_launch_version_execution",
                    }
                    and exc.browser_identity_check is not None
                ):
                    diagnostic["check"] = exc.browser_identity_check
                elif (
                    exc.browser_identity_phase
                    in {
                        "playwright_package_revision_identity",
                        "private_launch_version_execution",
                    }
                ):
                    diagnostic = None
        return _emit_error(
            classification,
            _compact_detail(exc),
            diagnostic=diagnostic,
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
    if argv and argv[0] == "voxblame-verify":
        return _verify_main(argv[1:])
    if argv and argv[0] == "voxblame-targets":
        return _targets_main(argv[1:])
    if argv and argv[0] == "voxblame-diff":
        return _diff_main(argv[1:])
    if argv and argv[0] == "voxblame-preview":
        return _preview_main(argv[1:])
    return _emit_error(
        "unsupported_command",
        "expected one of: voxblame-prepare-reference, voxblame-measure, "
        "voxblame-targets, voxblame-diff, voxblame-preview, voxblame-verify",
    )


if __name__ == "__main__":
    raise SystemExit(main())
