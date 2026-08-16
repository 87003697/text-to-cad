"""Batched headless-browser orchestration for all eight residual views."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
import json
import os
from pathlib import Path
import socket
from typing import Any

from meshshot import broker_client
from meshshot.broker_client import MeshGeometry, MeshshotError, RenderedPreview


_ORIGIN = "http://meshshot.local"
_RENDER_URL = f"{_ORIGIN}/render.html"
_ROUTE_GLOB = f"{_ORIGIN}/**"
_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
_BROWSER_STARTUP_TIMEOUT_MS = 15_000
_RENDER_TIMEOUT_MS = 120_000
_CONTRACT = json.loads(
    files("meshshot").joinpath("browser_contract.json").read_text(encoding="utf-8")
)
_AUTHORITY_PATH = Path(_CONTRACT["authorityPath"])
_SOCKET_PATH = Path(_CONTRACT["socketPath"])


def _load_browser_authority() -> dict[str, Any] | None:
    """Return the fixed formal authority, or None outside a formal job."""
    return broker_client.load_browser_authority(
        _AUTHORITY_PATH,
        open_file=os.open,
        fstat_file=os.fstat,
        read_file=os.read,
        close_file=os.close,
        effective_uid=os.getuid,
    )


def _registered_residual_render(
    authority: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Submit one exact residual request to the job-private program broker."""
    return broker_client.registered_residual_render(
        authority,
        payload,
        socket_path=_SOCKET_PATH,
        socket_factory=socket.socket,
    )


def _legacy_browser_render(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the existing local browser path outside a formal pilot job."""

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
    return result


def render_residual_preview(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    *,
    variant: str = "step",
    exterior_directions: Sequence[str] = (),
) -> RenderedPreview:
    """Render reference green and candidate red in one batched browser job."""

    loaded, payload, _ = broker_client.prepare_payload(
        reference, candidate, variant, exterior_directions
    )
    authority = _load_browser_authority()
    if authority is None:
        result = _legacy_browser_render(payload)
    else:
        result = _registered_residual_render(
            authority,
            broker_client.broker_payload(payload),
        )
    return broker_client.finalize_preview(result, loaded, variant)
