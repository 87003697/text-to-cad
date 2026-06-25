"""Export one CAD model to a single user-chosen file at an explicit path.

This is the user-facing "Export model" backend for the CAD Viewer: given a model
(an imported ``.step``/``.stp`` or a generated ``.step.py``), write exactly one of
STEP / 3MF / STL / GLB to an arbitrary ``--out`` destination — typically a path the
user picked from a native Save dialog.

It is deliberately distinct from :mod:`cadpy.step_artifact`, which only (re)builds the
hidden ``__cadcache__`` viewer GLB/topology package beside the source. This module
produces a single standalone file at an arbitrary location and writes **no** package or
beside-source artifacts. It reuses the same scene build + mesh + per-format exporters as
the ``scripts/step`` sidecar jobs (see ``generation.py``), so output is byte-equivalent to
the established sidecar pipeline — only the destination differs.

Emits a single final JSON line on stdout: ``{"ok": true, "path": ..., "filename": ...}``
or ``{"ok": false, "error": ...}`` (the Node spawner parses the last stdout JSON line).
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from pathlib import Path

from cadpy.catalog import source_from_path
from cadpy.cli_logging import CliLogger
from cadpy.generation import (
    EntrySpec,
    _entry_spec_from_source,
    _selector_options_for_part,
    run_script_generator,
)
from cadpy.glb import export_native_glb_from_scene
from cadpy.metadata import normalize_mesh_numeric
from cadpy.step_artifact import _build_entry_spec, _cad_ref_for_step, _infer_entry_kind
from cadpy.step_export import export_build123d_step_file
from cadpy.step_scene import (
    LoadedStepScene,
    load_step_scene,
    mesh_step_scene,
    scene_export_shape,
)
from cadpy.stl import export_part_stl_from_scene
from cadpy.threemf import export_part_3mf_from_scene

# Logical format name -> conventional file suffix (informational; the caller owns `--out`).
FORMAT_SUFFIX = {"step": ".step", "stl": ".stl", "3mf": ".3mf", "glb": ".glb"}


def _apply_mesh_overrides(
    spec: EntrySpec,
    mesh_tolerance: float | None,
    mesh_angular_tolerance: float | None,
) -> EntrySpec:
    if mesh_tolerance is None and mesh_angular_tolerance is None:
        return spec
    return replace(
        spec,
        mesh_tolerance=mesh_tolerance if mesh_tolerance is not None else spec.mesh_tolerance,
        mesh_angular_tolerance=(
            mesh_angular_tolerance
            if mesh_angular_tolerance is not None
            else spec.mesh_angular_tolerance
        ),
        mesh_tolerance_explicit=mesh_tolerance is not None or spec.mesh_tolerance_explicit,
        mesh_angular_tolerance_explicit=(
            mesh_angular_tolerance is not None or spec.mesh_angular_tolerance_explicit
        ),
    )


def _resolve_spec_and_scene(
    repo_root: Path,
    step_path: Path,
    source_path: Path | None,
    *,
    mesh_tolerance: float | None,
    mesh_angular_tolerance: float | None,
    logger: CliLogger,
) -> tuple[EntrySpec, LoadedStepScene]:
    """Build the entry spec + an in-memory scene for the model.

    Generated model (``--source-path`` given): run ``gen_step()`` in-process to build the
    scene — generated models keep no on-disk STEP. Imported model: load the existing STEP.
    """
    if source_path is not None:
        source = source_from_path(source_path)
        if source is None:
            raise RuntimeError(f"Python generator is not a gen_step() CAD source: {source_path}")
        spec = _entry_spec_from_source(source)
        if spec.step_path is None:
            raise RuntimeError(f"Generator defines no STEP output: {source_path}")
        # Align the logical STEP path/name when the caller passed an explicit --step that the
        # generator does not itself resolve to (mirrors cadpy.step_artifact).
        if spec.step_path.resolve() != step_path.resolve():
            spec = replace(
                spec,
                cad_ref=_cad_ref_for_step(repo_root, step_path),
                display_name=step_path.stem,
                step_path=step_path,
            )
        spec = _apply_mesh_overrides(spec, mesh_tolerance, mesh_angular_tolerance)
        scene = run_script_generator(spec, "gen_step", logger=logger, force=True)
        if scene is None:
            raise RuntimeError(f"Generator did not produce a STEP scene: {spec.source_ref}")
        return spec, scene

    if not step_path.is_file():
        raise FileNotFoundError(f"STEP file does not exist: {step_path}")
    with logger.timed(f"load STEP {step_path.name}"):
        scene = load_step_scene(step_path)
    kind = _infer_entry_kind(step_path, scene)
    spec = _build_entry_spec(
        repo_root,
        step_path,
        scene,
        kind=kind,
        mesh_tolerance=mesh_tolerance,
        mesh_angular_tolerance=mesh_angular_tolerance,
    )
    return spec, scene


def _export_scene(
    fmt: str,
    spec: EntrySpec,
    scene: LoadedStepScene,
    out: Path,
    selector_options,
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "step":
        # gen_step writes no STEP, so serialize the generator's in-memory compound; an
        # imported source already has a text STEP on disk, so copy it to the destination.
        source_compound = getattr(scene, "source_compound", None)
        if source_compound is not None:
            export_build123d_step_file(
                source_compound,
                out,
                text_to_cad_entry_kind=spec.kind,
                source_path=(str(getattr(scene, "source_path", "") or "") or None),
                source_hash=(str(getattr(scene, "source_hash", "") or "") or None),
            )
            return out
        if spec.step_path is not None and spec.step_path.is_file():
            if spec.step_path.resolve() != out.resolve():
                shutil.copyfile(spec.step_path, out)
            return out
        raise RuntimeError("No STEP geometry available to export")

    # Mesh once before the single-file mesh exporters (STL/3MF/native GLB), matching the
    # sidecar-job pipeline in generation.py. Per-occurrence colors ride on the meshed scene.
    mesh_step_scene(
        scene,
        linear_deflection=selector_options.linear_deflection,
        angular_deflection=selector_options.angular_deflection,
        relative=selector_options.relative,
    )
    scene_export_shape(scene)

    if fmt == "stl":
        return export_part_stl_from_scene(spec.step_path, scene, target_path=out)
    if fmt == "3mf":
        return export_part_3mf_from_scene(spec.step_path, scene, target_path=out, color=spec.color)
    if fmt == "glb":
        # User-facing GLB: native Y-up glTF for external tools, not the viewer's Z-up cad GLB.
        return export_native_glb_from_scene(
            spec.step_path,
            scene,
            target_path=out,
            linear_deflection=selector_options.linear_deflection,
            angular_deflection=selector_options.angular_deflection,
            color=spec.color,
        )
    raise ValueError(f"Unsupported export format: {fmt}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cadpy.step_export_target",
        description="Export one CAD model to STEP/3MF/STL/GLB at an explicit destination path.",
    )
    parser.add_argument("--repo-root", required=True, help="Repository/workspace root for relative metadata.")
    parser.add_argument("--step", required=True, help="Logical STEP path (generated) or on-disk STEP/STP (imported).")
    parser.add_argument("--source-path", help="Python gen_step() generator (.step.py) for a generated model.")
    parser.add_argument("--format", required=True, choices=tuple(FORMAT_SUFFIX), help="Output format.")
    parser.add_argument("--out", required=True, help="Destination file path for the exported model.")
    parser.add_argument("--mesh-tolerance", type=float, help="Override automatic mesh linear deflection.")
    parser.add_argument("--mesh-angular-tolerance", type=float, help="Override automatic mesh angular deflection.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed timing on stderr.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = CliLogger("step-export", verbose=bool(args.verbose))
    try:
        repo_root = Path(args.repo_root).expanduser().resolve()
        step_path = Path(args.step).expanduser().resolve()
        source_path = Path(args.source_path).expanduser().resolve() if args.source_path else None
        out = Path(args.out).expanduser().resolve()
        mesh_tolerance = normalize_mesh_numeric(args.mesh_tolerance, field_name="mesh_tolerance")
        mesh_angular_tolerance = normalize_mesh_numeric(
            args.mesh_angular_tolerance, field_name="mesh_angular_tolerance"
        )
        spec, scene = _resolve_spec_and_scene(
            repo_root,
            step_path,
            source_path,
            mesh_tolerance=mesh_tolerance,
            mesh_angular_tolerance=mesh_angular_tolerance,
            logger=logger,
        )
        selector_options = _selector_options_for_part(spec, scene=scene)
        written = _export_scene(args.format, spec, scene, out, selector_options)
    except Exception as exc:  # noqa: BLE001 — surface a clean JSON error to the Node caller.
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1
    print(
        json.dumps(
            {"ok": True, "path": str(written), "filename": written.name, "format": args.format},
            separators=(",", ":"),
        )
    )
    logger.total()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
