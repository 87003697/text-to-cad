"""Render a multi-view preview PNG of a single mesh file.

Delegates each view to `python skills/cad/scripts/snapshot` (Playwright +
Three.js pipeline) and composites the results into a 2x2 grid with Pillow.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_CLI = REPO_ROOT / "skills" / "cad" / "scripts" / "snapshot"

DEFAULT_CAMERAS = ("iso", "front", "right", "top")


def _ensure_glb(mesh_path: Path, tmp_dir: Path) -> Path:
    """cad snapshot's browser-side render only accepts `.glb`.

    For `.ply`/`.obj`/`.stl`/`.3mf`, load via trimesh and export a GLB
    sidecar into `tmp_dir`; return the path handed to `cad snapshot`.
    """
    if mesh_path.suffix.lower() in {".glb", ".gltf"}:
        return mesh_path
    import trimesh  # deferred import — mesh-preview runs against a real venv

    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"{mesh_path.stem}.glb"
    mesh = trimesh.load(str(mesh_path), force="mesh")
    mesh.export(str(out))
    return out


def _snapshot_view(mesh_path: Path, output: Path, camera: str) -> None:
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


def _composite_grid(image_paths: Sequence[Path], output: Path) -> None:
    from PIL import Image

    images = [Image.open(p).convert("RGBA") for p in image_paths]
    if len(images) != 4:
        raise ValueError(f"expected 4 tiles for 2x2 grid, got {len(images)}")
    w = min(img.width for img in images)
    h = min(img.height for img in images)
    tiles = [img.resize((w, h), Image.LANCZOS) if (img.width, img.height) != (w, h) else img for img in images]
    canvas = Image.new("RGBA", (w * 2, h * 2), (255, 255, 255, 255))
    for idx, tile in enumerate(tiles):
        row, col = divmod(idx, 2)
        canvas.paste(tile, (col * w, row * h))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output, format="PNG")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render a multi-view preview PNG of a mesh.")
    parser.add_argument("input", help="Path to mesh file (GLB/OBJ/STL/PLY/3MF/GLTF)")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument(
        "--cameras",
        default=",".join(DEFAULT_CAMERAS),
        help=f"Comma-separated camera presets for 4 tiles (default: {','.join(DEFAULT_CAMERAS)})",
    )
    args = parser.parse_args(argv)

    mesh_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]
    if len(cameras) != 4:
        print(
            json.dumps({"ok": False, "errors": ["--cameras must list exactly 4 preset names"]}),
        )
        return 2

    try:
        with tempfile.TemporaryDirectory(prefix="mesh_preview_") as tmp:
            tmp_dir = Path(tmp)
            glb_path = _ensure_glb(mesh_path, tmp_dir / "glb")
            tile_paths = []
            for idx, camera in enumerate(cameras):
                tile_path = tmp_dir / f"tile_{idx}_{camera}.png"
                _snapshot_view(glb_path, tile_path, camera)
                if not tile_path.exists():
                    # cad snapshot appends a timestamp before the extension; grep siblings.
                    candidates = sorted(tmp_dir.glob(f"tile_{idx}_{camera}*.png"))
                    if not candidates:
                        raise FileNotFoundError(f"snapshot did not produce a tile for camera={camera}")
                    tile_path = candidates[-1]
                tile_paths.append(tile_path)
            _composite_grid(tile_paths, output_path)
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"ok": False, "errors": [f"cad snapshot failed: {exc}"]}))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}))
        return 2

    print(json.dumps({"ok": True, "output": str(output_path), "cameras": cameras}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
