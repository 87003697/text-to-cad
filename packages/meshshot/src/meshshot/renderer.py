"""Batched headless-browser orchestration for all eight residual views."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
import json
import math
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image

from meshshot.browser_runtime import (
    BrowserRuntimeError,
    PrelaunchedCdpRuntime,
    default_executable,
)
from meshshot.profile import load_profile


_ORIGIN = "http://meshshot.local"
_RENDER_URL = f"{_ORIGIN}/render.html"
_ROUTE_GLOB = f"{_ORIGIN}/**"
_PAYLOAD_PATH = "/payload.json"
_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
_RENDER_TIMEOUT_MS = 120_000
_OUTSIDE_DIRECTIONS = frozenset({"-x", "+x", "-y", "+y", "-z", "+z"})

MeshshotPhase = Literal[
    "runtime",
    "dependency",
    "browser_launch",
    "browser_launch_process_limit",
    "browser_launch_file_limit",
    "browser_launch_address_space",
    "browser_launch_shared_memory",
    "browser_launch_executable",
    "browser_launch_executable_missing",
    "browser_launch_executable_permission",
    "browser_launch_executable_spawn_permission",
    "browser_launch_sandbox_permission",
    "browser_launch_filesystem_permission",
    "browser_launch_executable_dependency",
    "browser_adapter_profile",
    "browser_identity",
    "browser_profile",
    "browser_prelaunch",
    "browser_readiness",
    "browser_readiness_timeout",
    "browser_connect",
    "browser_cleanup",
    "browser_signal",
    "browser_render",
    "browser_result",
]


class MeshshotError(RuntimeError):
    """Stable renderer failure surfaced through the public preview command."""

    def __init__(self, message: str, *, phase: MeshshotPhase | None = None) -> None:
        super().__init__(message)
        self.phase = phase


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
    browser_runtime: dict[str, Any] | None = None


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
            + ", ".join(missing),
            phase="runtime",
        )

    payload = {
        "profile": loaded.profile,
        "variant": variant,
        "reference": reference.to_json(),
        "candidate": candidate.to_json(),
        "exteriorDirections": list(directions),
    }
    payload_bytes = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    renderer_events: list[str] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MeshshotError(
            "meshshot requires the Python playwright package and Chromium",
            phase="dependency",
        ) from exc

    browser_runtime: dict[str, Any] | None = None
    try:
        with sync_playwright() as playwright:
            configured_executable = os.environ.get("MESHSHOT_BROWSER_EXECUTABLE")
            executable = (
                Path(configured_executable)
                if configured_executable is not None
                else default_executable(playwright.chromium.executable_path)
            )
            runtime = PrelaunchedCdpRuntime(executable)
            with runtime.open(playwright.chromium) as browser:
                browser_runtime = dict(runtime.evidence)
                try:
                    context = browser.new_context(
                        viewport={"width": 64, "height": 64},
                        device_scale_factor=1,
                    )

                    def reject_outside_origin(route: Any) -> None:
                        try:
                            parsed = urlsplit(str(route.request.url))
                            same_origin = (
                                parsed.scheme == "http"
                                and parsed.hostname == "meshshot.local"
                                and (parsed.port or 80) == 80
                                and parsed.username is None
                                and parsed.password is None
                            )
                        except ValueError:
                            same_origin = False
                        if same_origin:
                            route.continue_()
                        else:
                            route.abort("blockedbyclient")

                    context.route("**/*", reject_outside_origin)
                    page = context.new_page()

                    def record_console(message: Any) -> None:
                        text = str(message.text)
                        if text.startswith("meshshot-stage:"):
                            renderer_events.append(text)

                    page.on("console", record_console)
                    page.on(
                        "crash",
                        lambda _: renderer_events.append("meshshot-stage:page-crash"),
                    )

                    def handle_route(route: Any) -> None:
                        request_path = route.request.url.removeprefix(_ORIGIN)
                        path = runtime_files.get(request_path)
                        if route.request.method != "GET":
                            route.fulfill(status=405, body="method not allowed")
                        elif request_path == _PAYLOAD_PATH:
                            route.fulfill(
                                status=200,
                                content_type="application/json",
                                headers={"cache-control": "no-store"},
                                body=payload_bytes,
                            )
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
                    result = page.evaluate("""
                        async () => {
                          console.info("meshshot-stage:payload-fetch:start");
                          const response = await fetch("/payload.json", { cache: "no-store" });
                          if (!response.ok) {
                            throw new Error(`meshshot payload fetch failed: ${response.status}`);
                          }
                          const renderPayload = await response.json();
                          console.info("meshshot-stage:payload-fetch:done");
                          return window.__meshshotRender(renderPayload);
                        }
                    """)
                    context.close()
                finally:
                    browser.close()
    except MeshshotError:
        raise
    except BrowserRuntimeError as exc:
        raise MeshshotError(
            f"headless residual browser runtime failed: {exc.operation}",
            phase=exc.operation,
        ) from exc
    except Exception as exc:
        stage = (
            f"; last renderer event: {renderer_events[-1]}"
            if renderer_events
            else "; no renderer stage event received"
        )
        raise MeshshotError(
            f"headless residual render failed{stage}",
            phase="browser_render",
        ) from exc

    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("error") if isinstance(result, dict) else None
        raise MeshshotError(
            str(detail or "browser returned an invalid render result"),
            phase="browser_result",
        )
    data_url = result.get("pngDataUrl")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
        raise MeshshotError(
            "browser returned invalid PNG data", phase="browser_result"
        )
    try:
        browser_png = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        with Image.open(BytesIO(browser_png)) as image:
            image.load()
            image = image.convert("RGB")
            expected = tuple(loaded.profile["variants"][variant]["image_pixels"])
            if image.size != expected:
                raise MeshshotError(
                    f"browser returned {image.size}, expected {expected} for {variant}",
                    phase="browser_result",
                )
            encoded = BytesIO()
            image.save(encoded, format="PNG", compress_level=9, optimize=False)
    except MeshshotError:
        raise
    except Exception as exc:
        raise MeshshotError(
            f"browser returned unreadable PNG data: {exc}",
            phase="browser_result",
        ) from exc
    views = result.get("views")
    expected_view_names = [view["name"] for view in loaded.profile["views"]]
    if (
        not isinstance(views, list)
        or len(views) != 8
        or [
            view.get("name") if isinstance(view, dict) else None
            for view in views
        ]
        != expected_view_names
    ):
        raise MeshshotError(
            "browser returned invalid view metadata", phase="browser_result"
        )
    return RenderedPreview(
        png_bytes=encoded.getvalue(),
        variant=variant,
        profile_sha256=loaded.sha256,
        views=tuple(dict(view) for view in views),
        browser_runtime=browser_runtime,
    )
