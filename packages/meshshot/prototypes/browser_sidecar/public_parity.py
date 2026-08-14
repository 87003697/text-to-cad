#!/usr/bin/env python3
"""Run the unchanged public meshshot projection with baseline or Sidecar transport."""

from __future__ import annotations

from contextlib import AbstractContextManager
from io import BytesIO
import hashlib
import json
import os
import sys
from types import SimpleNamespace
from typing import Any
from urllib.request import urlopen

from PIL import Image
import playwright.sync_api as playwright_api

sys.path.insert(0, "/opt/browser-sidecar/meshshot-src")
from meshshot import MeshGeometry, render_residual_preview  # noqa: E402


SCHEMA = "meshshot.browser-sidecar.render-request/2"
PUBLIC_RENDER_URL = "http://meshshot.local/render.html"
PUBLIC_ROUTE = "http://meshshot.local/**"
SIDECAR_RENDER_URL = "http://127.0.0.1:4174/render.html"


def exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} requires exact keys: {sorted(expected)}")
    return value


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("request exceeds 1 MiB")
    request = exact_keys(json.loads(raw), {"schema", "program", "payload"}, "request")
    if request["schema"] != SCHEMA or request["program"] != "residual":
        raise ValueError("public parity accepts only the residual Render Program")
    payload = exact_keys(
        request["payload"],
        {"reference", "candidate", "variant", "exteriorDirections", "options"},
        "residual payload",
    )
    exact_keys(payload["reference"], {"vertices", "faces"}, "reference")
    exact_keys(payload["candidate"], {"vertices", "faces"}, "candidate")
    options = exact_keys(payload["options"], {"cameraPolicy", "canonicalPostprocess"}, "options")
    if options != {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True}:
        raise ValueError("public parity requires fixed camera and canonical postprocess")
    return request


class PageAdapter:
    def __init__(self, page: Any) -> None:
        self._page = page

    def route(self, pattern: str, handler: Any) -> None:
        del handler
        if pattern != PUBLIC_ROUTE:
            raise ValueError("public renderer attempted an unregistered route")

    def goto(self, url: str, **kwargs: Any) -> Any:
        if url != PUBLIC_RENDER_URL:
            raise ValueError("public renderer attempted an unregistered URL")
        return self._page.goto(SIDECAR_RENDER_URL, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)


class ContextAdapter:
    def __init__(self, context: Any) -> None:
        self._context = context

    def new_page(self) -> PageAdapter:
        return PageAdapter(self._context.new_page())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


class BrowserAdapter:
    def __init__(self, browser: Any) -> None:
        self._browser = browser

    def new_context(self, **kwargs: Any) -> ContextAdapter:
        return ContextAdapter(self._browser.new_context(**kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)


class ChromiumAdapter:
    def __init__(self, chromium: Any, endpoint: str) -> None:
        self._chromium = chromium
        self._endpoint = endpoint

    def launch(self, **kwargs: Any) -> BrowserAdapter:
        allowed = {"headless", "args", "timeout"}
        if set(kwargs) != allowed or kwargs["headless"] is not True or kwargs["args"] != ["--no-sandbox"]:
            raise ValueError("public renderer changed its frozen launch contract")
        return BrowserAdapter(self._chromium.connect(self._endpoint, timeout=kwargs["timeout"]))


class RemoteSyncPlaywright(AbstractContextManager[Any]):
    def __init__(self, real_sync_playwright: Any, endpoint: str) -> None:
        self._manager = real_sync_playwright()
        self._endpoint = endpoint

    def __enter__(self) -> Any:
        real = self._manager.__enter__()
        return SimpleNamespace(chromium=ChromiumAdapter(real.chromium, self._endpoint))

    def __exit__(self, *args: Any) -> Any:
        return self._manager.__exit__(*args)


def remote_endpoint() -> str:
    host = os.environ.get("BROWSER_SIDECAR_HOST", "sidecar")
    job_id = os.environ.get("BROWSER_SIDECAR_JOB_ID", "")
    if host != "sidecar" or not job_id:
        raise ValueError("remote public parity requires sealed sidecar authority")
    with urlopen(f"http://{host}:3001/v1/authority", timeout=10) as response:
        authority = json.load(response)
    if authority.get("schema") != "meshshot.browser-sidecar.prototype/1" or authority.get("jobId") != job_id:
        raise ValueError("remote public parity authority mismatch")
    return f"ws://{host}:3000{authority['endpointPath']}"


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"baseline", "remote"}:
        raise ValueError("usage: public_parity.py <baseline|remote>")
    mode = sys.argv[1]
    request = read_request()
    payload = request["payload"]
    if mode == "remote":
        real_sync_playwright = playwright_api.sync_playwright
        endpoint = remote_endpoint()
        playwright_api.sync_playwright = lambda: RemoteSyncPlaywright(real_sync_playwright, endpoint)
    rendered = render_residual_preview(
        MeshGeometry(**payload["reference"]),
        MeshGeometry(**payload["candidate"]),
        variant=payload["variant"],
        exterior_directions=payload["exteriorDirections"],
    )
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        image.load()
        image_mode = image.mode
        image_size = list(image.size)
    projection = {
        "variant": rendered.variant,
        "profileSha256": rendered.profile_sha256,
        "views": list(rendered.views),
    }
    projection_bytes = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    print(json.dumps({
        "ok": True,
        "mode": mode,
        "publicCallable": "meshshot.render_residual_preview",
        "renderedType": type(rendered).__name__,
        "pngBytes": len(rendered.png_bytes),
        "pngSha256": hashlib.sha256(rendered.png_bytes).hexdigest(),
        "imageMode": image_mode,
        "imageSize": image_size,
        "projection": projection,
        "evidenceSha256": hashlib.sha256(projection_bytes).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
