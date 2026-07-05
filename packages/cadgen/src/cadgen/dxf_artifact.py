from __future__ import annotations

import argparse
import json
from pathlib import Path

from cadgen.cli_logging import CliLogger
from cadgen._internal.drawing_package import (
    drawing_package_current,
    export_drawing_dxf,
    load_drawing_descriptor,
)
from cadgen._internal.generation import (
    _entry_spec_from_source,
    run_script_generator,
)
from cadgen.catalog import (
    drawing_package_path_for_source,
    is_dxf_generator_path,
    source_from_path,
)
from cadgen.render import relative_to_cwd


def _result_payload(
    script_path: Path,
    *,
    descriptor: dict[str, object] | None,
    skipped: bool = False,
) -> dict[str, object]:
    package_dir = drawing_package_path_for_source(script_path)
    payload: dict[str, object] = {
        "ok": True,
        "sourcePath": relative_to_cwd(script_path),
        "packagePath": relative_to_cwd(package_dir),
        "sourceKind": "python",
    }
    if descriptor:
        dxf_ref = str(descriptor.get("dxf") or "").strip()
        if dxf_ref:
            payload["dxfPath"] = relative_to_cwd(package_dir / dxf_ref)
        source_hash = str(descriptor.get("sourceHash") or "").strip()
        if source_hash:
            payload["sourceHash"] = source_hash
        dxf_hash = str(descriptor.get("dxfHash") or "").strip()
        if dxf_hash:
            payload["dxfHash"] = dxf_hash
    if skipped:
        payload["skipped"] = True
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cadgen.dxf_artifact",
        description="Generate the CAD Viewer drawing-package artifact for one generated DXF entry.",
    )
    parser.add_argument("--repo-root", default=".", help="Repository/workspace root (accepted for CLI parity with step_artifact).")
    parser.add_argument("--source-path", required=True, help="Python gen_dxf() generator source path.")
    parser.add_argument("--export", help="Also write the (fresh) drawing DXF to this path.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if a current artifact exists.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed timing on stderr.")
    return parser




def build_dxf_artifact(
    *,
    repo_root: Path,
    source_path: Path,
    export: Path | None = None,
    force: bool = False,
    reset_runtime_closure: bool = False,
    logger: CliLogger | None = None,
) -> dict[str, object]:
    """Build the drawing-package artifact for one gen_dxf() generator and RETURN the
    result payload (the exact dict the CLI prints). Mirrors
    :func:`cadgen.step_artifact.build_step_artifact` for the DXF pipeline. With
    ``export`` set, the fresh drawing DXF is also written to that path."""
    del repo_root  # payload paths are cwd-relative; kept for CLI parity with step_artifact
    script_path = Path(source_path).expanduser().resolve()
    if not script_path.is_file():
        raise FileNotFoundError(f"Python generator does not exist: {script_path}")
    source = source_from_path(script_path)
    if source is None or source.kind != "dxf" or source.generator_metadata is None:
        raise RuntimeError(f"Python generator is not a gen_dxf() CAD source: {script_path}")
    if not is_dxf_generator_path(script_path):
        raise RuntimeError(
            f"Viewer drawing artifacts require a dedicated <name>.dxf.py generator: {script_path}"
        )
    if logger is None:
        logger = CliLogger("dxf-artifact", verbose=False)
    package_dir = drawing_package_path_for_source(script_path)
    skipped = not force and drawing_package_current(script_path)
    if not skipped:
        spec = _entry_spec_from_source(source)
        run_script_generator(
            spec, "gen_dxf", logger=logger, force=force, reset_runtime_closure=reset_runtime_closure
        )
    descriptor = load_drawing_descriptor(package_dir)
    if descriptor is None:
        raise RuntimeError(f"gen_dxf() did not produce a drawing package: {relative_to_cwd(package_dir)}")
    payload = _result_payload(script_path, descriptor=descriptor, skipped=skipped)
    if export is not None:
        export_path = export_drawing_dxf(script_path, Path(export))
        payload["path"] = str(export_path)
        payload["filename"] = export_path.name
    return payload


def run_cli_payload(
    argv: list[str] | None = None,
    *,
    reset_runtime_closure: bool = False,
) -> dict[str, object]:
    """Parse CLI ``argv`` and run :func:`build_dxf_artifact`, RETURNING its payload.
    The in-process primitive shared by ``main()`` and the CAD Viewer's warm worker —
    the worker passes ``reset_runtime_closure=True`` so repeated warm builds record
    the same closure a cold CLI does."""
    args = build_parser().parse_args(argv)
    logger = CliLogger("dxf-artifact", verbose=bool(args.verbose))
    payload = build_dxf_artifact(
        repo_root=Path(args.repo_root),
        source_path=Path(args.source_path),
        export=Path(args.export) if args.export else None,
        force=bool(args.force),
        reset_runtime_closure=reset_runtime_closure,
        logger=logger,
    )
    logger.total()
    return payload


def main(argv: list[str] | None = None) -> int:
    payload = run_cli_payload(argv)
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
