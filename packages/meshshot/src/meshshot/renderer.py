"""Batched headless-browser orchestration for all eight residual views."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import socket
import stat
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

from meshshot import broker_client
from meshshot.broker_client import MeshGeometry, MeshshotError, RenderedPreview


_ORIGIN = "http://meshshot.local"
_RENDER_URL = f"{_ORIGIN}/render.html"
_ROUTE_GLOB = f"{_ORIGIN}/**"
_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
_BROWSER_STARTUP_TIMEOUT_MS = 15_000
_CONTRACT = json.loads(
    files("meshshot").joinpath("browser_contract.json").read_text(encoding="utf-8")
)
_AUTHORITY_PATH = Path(_CONTRACT["authorityPath"])
_SOCKET_PATH = Path(_CONTRACT["socketPath"])
_RUNTIME_CAPABILITY_PATH = Path("/run/meshshot-browser/runtime.json")
_RUNTIME_CAPABILITY_SCHEMA = "text-to-cad.browser-runtime-capability/1"
_RUNTIME_REQUEST_SCHEMA = "text-to-cad.cad-render-request/1"
_RUNTIME_RESPONSE_SCHEMA = "text-to-cad.cad-render-response/1"
_HEX_12_TO_64 = re.compile(r"[0-9a-f]{12,64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_RUNTIME_CAPABILITY_BYTES = 16 * 1024
_MAX_RUNTIME_REQUEST_BYTES = 96 * 1024 * 1024
_MAX_RUNTIME_RESPONSE_BYTES = 16 * 1024 * 1024
_RUNTIME_TIMEOUT_SECONDS = 120.0
_LOOPBACK_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))


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


def _load_runtime_capability() -> dict[str, Any] | None:
    """Read the fixed Development Browser Runtime capability, if present."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(_RUNTIME_CAPABILITY_PATH, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MeshshotError("browser runtime capability is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o444
        ):
            raise MeshshotError("browser runtime capability is replaceable")
        raw = os.read(descriptor, _MAX_RUNTIME_CAPABILITY_BYTES + 1)
    except MeshshotError:
        raise
    except OSError as exc:
        raise MeshshotError("browser runtime capability is unavailable") from exc
    finally:
        os.close(descriptor)
    if not raw or len(raw) > _MAX_RUNTIME_CAPABILITY_BYTES:
        raise MeshshotError("browser runtime capability has an invalid size")
    try:
        capability = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeshshotError("browser runtime capability is not valid JSON") from exc
    expected_keys = {
        "schema",
        "jobId",
        "imageRef",
        "mcpUrl",
        "cadRenderUrl",
        "cadRenderToken",
        "programs",
    }
    if not isinstance(capability, dict) or set(capability) != expected_keys:
        raise MeshshotError("browser runtime capability has an invalid schema")
    programs = capability["programs"]
    endpoint = capability["cadRenderUrl"]
    mcp_endpoint = capability["mcpUrl"]
    endpoint_valid = _valid_loopback_url(endpoint, "/cad/render/residual")
    mcp_endpoint_valid = _valid_loopback_url(mcp_endpoint, "/mcp")
    if (
        capability["schema"] != _RUNTIME_CAPABILITY_SCHEMA
        or not isinstance(capability["jobId"], str)
        or _HEX_12_TO_64.fullmatch(capability["jobId"]) is None
        or not isinstance(capability["imageRef"], str)
        or not capability["imageRef"].isascii()
        or not capability["imageRef"]
        or len(capability["imageRef"]) > 256
        or not isinstance(capability["mcpUrl"], str)
        or not mcp_endpoint_valid
        or not isinstance(capability["cadRenderToken"], str)
        or _HEX_12_TO_64.fullmatch(capability["cadRenderToken"]) is None
        or not isinstance(programs, dict)
        or set(programs) != {"residual"}
        or not isinstance(programs["residual"], str)
        or _SHA256.fullmatch(programs["residual"]) is None
        or not endpoint_valid
    ):
        raise MeshshotError("browser runtime capability identity is invalid")
    return capability


def _valid_loopback_url(value: Any, expected_path: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.username is None
        and parsed.password is None
        and port is not None
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.fragment
    )


def _runtime_browser_render(
    capability: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Call the fixed residual operation in the Development Browser Runtime."""

    request_value = {
        "schema": _RUNTIME_REQUEST_SCHEMA,
        "jobId": capability["jobId"],
        "program": "residual",
        "programDigest": capability["programs"]["residual"],
        "payload": broker_client.broker_payload(payload),
    }
    request_bytes = json.dumps(
        request_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if len(request_bytes) > _MAX_RUNTIME_REQUEST_BYTES:
        raise MeshshotError("browser runtime residual request is too large")
    request = urllib_request.Request(
        capability["cadRenderUrl"],
        data=request_bytes,
        headers={
            "authorization": f"Bearer {capability['cadRenderToken']}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with _LOOPBACK_OPENER.open(request, timeout=_RUNTIME_TIMEOUT_SECONDS) as response:
            response_bytes = response.read(_MAX_RUNTIME_RESPONSE_BYTES + 1)
    except (OSError, urllib_error.URLError) as exc:
        raise MeshshotError("browser runtime residual request failed") from exc
    if len(response_bytes) > _MAX_RUNTIME_RESPONSE_BYTES:
        raise MeshshotError("browser runtime residual response is too large")
    try:
        response_value = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeshshotError("browser runtime residual response is invalid") from exc
    if (
        not isinstance(response_value, dict)
        or set(response_value)
        != {"schema", "jobId", "program", "programDigest", "result"}
        or response_value["schema"] != _RUNTIME_RESPONSE_SCHEMA
        or response_value["jobId"] != capability["jobId"]
        or response_value["program"] != "residual"
        or response_value["programDigest"] != capability["programs"]["residual"]
        or not isinstance(response_value["result"], dict)
    ):
        raise MeshshotError("browser runtime residual response identity is invalid")
    return response_value["result"]


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
                # Residual rendering is data-dependent. The Workspace command
                # owns cancellation; Playwright must not impose a second
                # page-level deadline on the same operation.
                page.goto(_RENDER_URL, wait_until="load", timeout=0)
                page.wait_for_function(
                    "typeof window.__meshshotRender === 'function'",
                    timeout=0,
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
    if authority is not None:
        result = _registered_residual_render(
            authority,
            broker_client.broker_payload(payload),
        )
    else:
        runtime_capability = _load_runtime_capability()
        if runtime_capability is not None:
            result = _runtime_browser_render(runtime_capability, payload)
        else:
            result = _legacy_browser_render(payload)
    return broker_client.finalize_preview(result, loaded, variant)
