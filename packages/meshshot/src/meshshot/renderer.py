"""Batched headless-browser orchestration for all eight residual views."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
from typing import Any

from PIL import Image

from meshshot.profile import load_profile


_ORIGIN = "http://meshshot.local"
_RENDER_URL = f"{_ORIGIN}/render.html"
_ROUTE_GLOB = f"{_ORIGIN}/**"
_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
_BROWSER_STARTUP_TIMEOUT_MS = 15_000
_RENDER_TIMEOUT_MS = 120_000
_OUTSIDE_DIRECTIONS = frozenset({"-x", "+x", "-y", "+y", "-z", "+z"})


class MeshshotError(RuntimeError):
    """Stable renderer failure surfaced through the public preview command."""


@dataclass(frozen=True)
class MeshGeometry:
    """JSON-safe indexed triangles in the canonical renderer frame."""

    vertices: Sequence[Sequence[float]]
    faces: Sequence[Sequence[int]]

    def to_json(self) -> dict[str, list[list[float]] | list[list[int]]]:
        vertices = [[float(value) for value in vertex] for vertex in self.vertices]
        faces = [[int(value) for value in face] for face in self.faces]
        if not vertices or any(
            len(vertex) != 3 or not all(math.isfinite(value) for value in vertex)
            for vertex in vertices
        ):
            raise MeshshotError("mesh geometry requires finite three-dimensional vertices")
        if not faces or any(
            len(face) != 3
            or any(index < 0 or index >= len(vertices) for index in face)
            for face in faces
        ):
            raise MeshshotError("mesh geometry requires valid triangle indices")
        return {"vertices": vertices, "faces": faces}


@dataclass(frozen=True)
class RenderedPreview:
    """Opaque RGB PNG bytes and semantic facts returned by the browser core."""

    png_bytes: bytes
    variant: str
    profile_sha256: str
    views: tuple[dict[str, Any], ...]


def render_residual_preview(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    *,
    variant: str = "step",
    exterior_directions: Sequence[str] = (),
) -> RenderedPreview:
    """Render reference green and candidate red in one batched browser job."""

    loaded = load_profile()
    if variant not in loaded.profile["variants"]:
        raise MeshshotError(f"unsupported render variant: {variant}")
    directions = tuple(str(value) for value in exterior_directions)
    if len(set(directions)) != len(directions) or any(
        value not in _OUTSIDE_DIRECTIONS for value in directions
    ):
        raise MeshshotError("exterior directions must be unique signed x/y/z values")
    runtime_files = {
        "/render.html": _RUNTIME_DIR / "render.html",
        "/residual-render.js": _RUNTIME_DIR / "residual-render.js",
    }
    missing = [str(path) for path in runtime_files.values() if not path.is_file()]
    if missing:
        raise MeshshotError(
            "meshshot browser runtime is missing; run the mesh-compare bundle: "
            + ", ".join(missing)
        )

    payload = {
        "profile": loaded.profile,
        "variant": variant,
        "reference": reference.to_json(),
        "candidate": candidate.to_json(),
        "exteriorDirections": list(directions),
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MeshshotError(
            "meshshot requires the Python playwright package and Chromium"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
                timeout=_BROWSER_STARTUP_TIMEOUT_MS,
            )
            try:
                context = browser.new_context(
                    viewport={"width": 64, "height": 64},
                    device_scale_factor=1,
                )
                page = context.new_page()

                def handle_route(route: Any) -> None:
                    path = runtime_files.get(route.request.url.removeprefix(_ORIGIN))
                    if route.request.method != "GET":
                        route.fulfill(status=405, body="method not allowed")
                    elif path is None or not path.is_file():
                        route.fulfill(status=404, body="not found")
                    else:
                        content_type = (
                            "text/html; charset=utf-8"
                            if path.suffix == ".html"
                            else "text/javascript; charset=utf-8"
                        )
                        route.fulfill(
                            status=200,
                            content_type=content_type,
                            headers={"cache-control": "no-store"},
                            body=path.read_bytes(),
                        )

                page.route(_ROUTE_GLOB, handle_route)
                page.goto(_RENDER_URL, wait_until="load", timeout=_RENDER_TIMEOUT_MS)
                page.wait_for_function(
                    "typeof window.__meshshotRender === 'function'",
                    timeout=_RENDER_TIMEOUT_MS,
                )
                result = page.evaluate(
                    "(renderPayload) => window.__meshshotRender(renderPayload)",
                    payload,
                )
                context.close()
            finally:
                browser.close()
    except MeshshotError:
        raise
    except Exception as exc:
        raise MeshshotError(f"headless residual render failed: {exc}") from exc

    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("error") if isinstance(result, dict) else None
        raise MeshshotError(str(detail or "browser returned an invalid render result"))
    data_url = result.get("pngDataUrl")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
        raise MeshshotError("browser returned invalid PNG data")
    try:
        browser_png = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        with Image.open(BytesIO(browser_png)) as image:
            image.load()
            image = image.convert("RGB")
            expected = tuple(loaded.profile["variants"][variant]["image_pixels"])
            if image.size != expected:
                raise MeshshotError(
                    f"browser returned {image.size}, expected {expected} for {variant}"
                )
            encoded = BytesIO()
            image.save(encoded, format="PNG", compress_level=9, optimize=False)
    except MeshshotError:
        raise
    except Exception as exc:
        raise MeshshotError(f"browser returned unreadable PNG data: {exc}") from exc
    views = result.get("views")
    if not isinstance(views, list) or len(views) != 8:
        raise MeshshotError("browser returned invalid view metadata")
    return RenderedPreview(
        png_bytes=encoded.getvalue(),
        variant=variant,
        profile_sha256=loaded.sha256,
        views=tuple(dict(view) for view in views),
    )
