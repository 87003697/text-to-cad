"""Mesh comparison visualization CLI.

Two subcommands:
- `heatmap` — colorize `mesh_a` by per-vertex distance to `mesh_b`,
  render the resulting distance-colored GLB via `cad snapshot`.
- `side-by-side` — render `mesh_a` and `mesh_b` at each of the
  requested camera presets, then Pillow-composite into one PNG.

Both modes delegate the actual 3D render to `skills/cad/scripts/snapshot`
so the visual style matches `$cad-viewer` and every other repo render.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from meshscope.compare import prepare, vertex_distances
from meshscope.viz import colorize, side_by_side


REPO_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_CLI = REPO_ROOT / "skills" / "cad" / "scripts" / "snapshot"


def _ensure_glb(mesh_path: Path, tmp_dir: Path) -> Path:
    """cad snapshot's browser-side render only accepts `.glb` / `.gltf`.

    For other formats, load via trimesh and export a GLB sidecar.
    """
    if mesh_path.suffix.lower() in {".glb", ".gltf"}:
        return mesh_path
    import trimesh

    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"{mesh_path.stem}.glb"
    mesh = trimesh.load(str(mesh_path), force="mesh")
    mesh.export(str(out))
    return out


def _snapshot(mesh_path: Path, output: Path, camera: str) -> Path:
    """Invoke cad snapshot for a single (mesh, camera) → PNG render.

    cad snapshot appends a UTC timestamp before the extension; we glob
    siblings after the call and return the actual file path.
    """
    cmd = [
        sys.executable,
        str(SNAPSHOT_CLI),
        "--input",
        str(mesh_path),
        "--output",
        str(output),
        "--mode",
        "view",
        "--camera",
        camera,
        "--size-profile",
        "simple-square",
    ]
    subprocess.run(cmd, check=True)
    if output.exists():
        return output
    stem = output.stem
    siblings = sorted(output.parent.glob(f"{stem}*{output.suffix}"))
    if not siblings:
        raise FileNotFoundError(f"cad snapshot produced no PNG for {mesh_path.name} @ camera={camera}")
    return siblings[-1]


def _cmd_heatmap(args) -> dict:
    pair = prepare(args.mesh_a, args.mesh_b)
    dists = vertex_distances(pair, n_samples=args.samples)
    colored_glb = colorize(pair.norm_a, dists)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    produced = _snapshot(colored_glb, output, args.camera)
    return {"ok": True, "mode": "heatmap", "output": str(produced)}


def _cmd_side_by_side(args) -> dict:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]
    if not cameras:
        raise ValueError("--cameras must list at least one camera preset")

    with tempfile.TemporaryDirectory(prefix="mesh_render_") as tmp:
        tmp_dir = Path(tmp)
        glb_a = _ensure_glb(Path(args.mesh_a), tmp_dir / "glb")
        glb_b = _ensure_glb(Path(args.mesh_b), tmp_dir / "glb")
        rows: list[Path] = []
        for cam in cameras:
            per_cam_tiles: list[Path] = []
            per_cam_labels: list[str] = []
            for label, mesh_path in (("A", glb_a), ("B", glb_b)):
                tile = tmp_dir / f"{label}_{cam}.png"
                produced = _snapshot(mesh_path, tile, cam)
                per_cam_tiles.append(produced)
                per_cam_labels.append(f"{label} · {cam}")
            row_out = tmp_dir / f"row_{cam}.png"
            side_by_side(per_cam_tiles, labels=per_cam_labels, output=row_out)
            rows.append(row_out)

        _stack_rows_vertically(rows, output)
    return {"ok": True, "mode": "side-by-side", "output": str(output), "cameras": cameras}


def _stack_rows_vertically(rows: Sequence[Path], output: Path) -> None:
    from PIL import Image

    imgs = [Image.open(str(p)) for p in rows]
    w = max(im.width for im in imgs)
    h = sum(im.height for im in imgs)
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height
    canvas.save(str(output))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render mesh comparison visualizations.")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_heat = sub.add_parser("heatmap", help="Distance-colored render of mesh_a")
    p_heat.add_argument("mesh_a")
    p_heat.add_argument("mesh_b")
    p_heat.add_argument("--output", required=True, help="Output PNG path")
    p_heat.add_argument("--samples", type=int, default=10000)
    p_heat.add_argument("--camera", default="iso")

    p_side = sub.add_parser("side-by-side", help="Multi-view A|B composite")
    p_side.add_argument("mesh_a")
    p_side.add_argument("mesh_b")
    p_side.add_argument("--output", required=True, help="Output PNG path")
    p_side.add_argument("--cameras", default="iso,front,right,top")

    args = parser.parse_args(argv)

    try:
        if args.mode == "heatmap":
            result = _cmd_heatmap(args)
        elif args.mode == "side-by-side":
            result = _cmd_side_by_side(args)
        else:  # pragma: no cover
            raise ValueError(f"unknown mode: {args.mode}")
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"ok": False, "errors": [f"cad snapshot failed: {exc}"]}))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}))
        return 2

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
