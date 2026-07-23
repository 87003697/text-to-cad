"""Distance colorization + Pillow side-by-side composite for mesh compare.

`viz.py` deliberately holds no headless-render logic: distance-colored GLBs
are handed off to `skills/cad/scripts/snapshot` for rendering, and PNG
composition uses only Pillow. Import `PIL` is deferred so importing this
module does not fail when the `[viz]` extra is not installed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import trimesh


def _distance_to_rgba(
    distances: np.ndarray,
    vmin: float | None = None,
    vmax: float | None = None,
) -> np.ndarray:
    """Map a distance array to blue→green→red RGBA (uint8).

    Values outside `[vmin, vmax]` are clipped. `vmax` defaults to the 95th
    percentile so extreme outliers do not compress the color scale.
    """
    if vmin is None:
        vmin = 0.0
    if vmax is None:
        vmax = float(np.percentile(distances, 95))
    denom = max(vmax - vmin, 1e-10)
    norm = np.clip((distances - vmin) / denom, 0, 1)
    r = np.where(norm < 0.5, norm * 2 * 255, 255).astype(np.uint8)
    g = np.where(norm < 0.5, norm * 2 * 255, (1 - norm) * 2 * 255).astype(np.uint8)
    b = np.where(norm < 0.5, 255, (1 - norm) * 2 * 255).astype(np.uint8)
    a = np.full_like(r, 255)
    return np.column_stack([r, g, b, a])


def colorize(
    mesh: trimesh.Trimesh,
    distances: np.ndarray,
    vmin: float | None = None,
    vmax: float | None = None,
) -> Path:
    """Export a distance-colored GLB copy of `mesh` to a temp file.

    Typical usage feeds the returned path to `cad snapshot` to render a
    heatmap PNG. The caller is responsible for cleanup of the temp dir.
    """
    colored = mesh.copy()
    colors = _distance_to_rgba(distances, vmin=vmin, vmax=vmax)
    colored.visual.vertex_colors = colors
    tmp_dir = Path(tempfile.mkdtemp(prefix="meshscope_"))
    out = tmp_dir / "heatmap.glb"
    colored.export(str(out))
    return out


def side_by_side(
    img_paths: list[Path | str],
    labels: list[str] | None = None,
    output: Path | str | None = None,
) -> Path:
    """Horizontally composite N PNGs into one, with optional labels on top."""
    from PIL import Image, ImageDraw

    images = [Image.open(str(p)) for p in img_paths]
    h = max(im.height for im in images)
    label_h = 30 if labels else 0
    total_w = sum(im.width for im in images)
    canvas = Image.new("RGB", (total_w, h + label_h), (255, 255, 255))

    x = 0
    for i, im in enumerate(images):
        canvas.paste(im, (x, label_h))
        if labels and i < len(labels):
            draw = ImageDraw.Draw(canvas)
            draw.text((x + 10, 5), labels[i], fill=(0, 0, 0))
        x += im.width

    if output is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="meshscope_"))
        output = tmp_dir / "comparison.png"
    else:
        output = Path(output)
    canvas.save(str(output))
    return output
