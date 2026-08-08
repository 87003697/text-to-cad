"""Render a drawing to a still or turntable, the way the CAD skill renders a STEP model.

A drawing has no geometry of its own to render: what the viewport shows is the 3D flat
pattern baked into its package as ``preview.glb``. So this CLI does two things — make the
package current (the same build ``scripts/artifact`` and the CAD Viewer run), then hand the
package's mesh to the shared render core.

The render half is ``cadgen.snapshot_core``, shared with the CAD skill rather than copied:
a drawing's preview is a plain mesh, so it takes the same mesh path a direct .glb input
takes there. What stays here is only the part that is about drawings — resolving a `.dxf`
or a `.dxf.py` to a built package.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cadgen.snapshot_core import (
    SnapshotError,
    normalize_size_profile,
    path_is_inside_or_equal,
    positive_integer,
    print_render_result,
    render_resolved_job_packet,
    resolve_mesh_render_job,
    snapshot_timestamp,
    write_render_outputs,
)

RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
SUPPORTED_MODES = ("view", "orbit")


def build_dxf_artifact(*args, **kwargs):
    from cadgen.dxf_artifact import build_dxf_artifact as build

    return build(*args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/snapshot",
        description=(
            "Render a drawing's 3D flat pattern to a PNG still or an orbit GIF. The input "
            "is an imported .dxf or a gen_dxf() Python source; its drawing package is built "
            "or refreshed first, then the package's preview.glb is rendered. Drawings carry "
            "no CAD topology, so there are no selector, parameter, section or exploded "
            "options — those belong to the CAD skill's STEP snapshot."
        ),
    )
    parser.add_argument("--input", "-i", required=True, help="An imported .dxf or a gen_dxf() Python source.")
    parser.add_argument("--output", "-o", required=True, help="Output .png (view mode) or .gif (orbit mode).")
    parser.add_argument("--mode", default="view", choices=SUPPORTED_MODES, help="view (default) or orbit.")
    parser.add_argument("--camera", help="A preset name, an azimuth:elevation pair, or a JSON camera object.")
    parser.add_argument("--appearance", help="A saved theme name, inline appearance JSON, or a JSON file path.")
    parser.add_argument("--size-profile", dest="size_profile", help="Default dimensions, e.g. simple or presentation.")
    parser.add_argument("--width", type=int, help="Render width in pixels.")
    parser.add_argument("--height", type=int, help="Render height in pixels.")
    parser.add_argument("--force", action="store_true", help="Rebuild the drawing package even when it is current.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print the render result as JSON.")
    return parser


def preview_path_for_input(source: Path, *, force: bool) -> Path:
    """Build/refresh the drawing package and return the preview.glb inside it."""
    lowered = source.name.lower()
    if not lowered.endswith(".dxf") and not lowered.endswith(".py"):
        raise SnapshotError(
            f"snapshot input must be a .dxf file or a gen_dxf() Python source: {source}"
        )
    if not source.is_file():
        raise SnapshotError(f"snapshot input does not exist: {source}")
    payload = build_dxf_artifact(repo_root=Path.cwd(), source_path=source, force=force)
    preview = str(payload.get("previewPath") or "").strip()
    if not preview:
        # The builder always bakes a preview; a payload without one means the package is
        # older than preview.glb existed, and rendering the stale package would be worse
        # than saying so.
        raise SnapshotError(
            f"drawing package for {source} has no preview.glb; rebuild it with --force"
        )
    return Path(preview).resolve()


def resolve_job(args: argparse.Namespace, *, cwd: Path) -> dict[str, object]:
    source = Path(args.input).expanduser()
    if not source.is_absolute():
        source = (cwd / source).resolve()
    preview = preview_path_for_input(source, force=bool(args.force))

    job: dict[str, object] = {"mode": args.mode, "outputs": [{"path": args.output}]}
    if args.camera:
        job["camera"] = args.camera
    if args.appearance:
        job["appearance"] = args.appearance
    if args.size_profile:
        job["sizeProfile"] = normalize_size_profile(args.size_profile)
    if args.width is not None:
        job["width"] = positive_integer(args.width, "--width")
    if args.height is not None:
        job["height"] = positive_integer(args.height, "--height")

    # The preview lives inside the hidden package, which is never under the caller's cwd in
    # any useful sense, so serve it from its own directory.
    root_path = cwd if path_is_inside_or_equal(preview, cwd) else preview.parent
    return resolve_mesh_render_job(
        job,
        kind="glb",
        input_path=preview,
        root_path=root_path,
        resolved_cwd=cwd,
        timestamp=snapshot_timestamp(),
    )


async def run_async(args: argparse.Namespace, *, cwd: Path, stdout: Any) -> int:
    job = resolve_job(args, cwd=cwd)
    result = await render_resolved_job_packet(
        {"single": True, "jobs": [job]}, runtime_dir=RUNTIME_DIR
    )
    write_render_outputs(result)
    print_render_result(result, json_output=bool(args.json_output), stdout=stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return asyncio.run(run_async(args, cwd=Path.cwd(), stdout=sys.stdout))
    except SnapshotError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback at users
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
