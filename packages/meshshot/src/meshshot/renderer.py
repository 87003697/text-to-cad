"""Batched headless-browser orchestration for all eight residual views."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
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
_CONTRACT = json.loads(
    files("meshshot").joinpath("browser_contract.json").read_text(encoding="utf-8")
)
_AUTHORITY_PATH = Path(_CONTRACT["authorityPath"])
_SOCKET_PATH = Path(_CONTRACT["socketPath"])
_AUTHORITY_SCHEMA = _CONTRACT["authoritySchema"]
_REQUEST_SCHEMA = _CONTRACT["requestSchema"]
_RESPONSE_SCHEMA = _CONTRACT["responseSchema"]
_SIDECAR_IMAGE_ID = _CONTRACT["sidecarImageId"]
_PROGRAMS = _CONTRACT["programs"]
_JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
_GATE_NONCE = re.compile(r"[0-9a-f]{16,64}\Z")
_MAX_AUTHORITY_BYTES = 16 * 1024
_MAX_REQUEST_BYTES = _CONTRACT["maxRequestBytes"]
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_SOCKET_TIMEOUT_SECONDS = 120.0


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


def _strict_json(payload: bytes, label: str) -> Any:
    """Decode one duplicate-free JSON value from a bounded trusted channel."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MeshshotError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeshshotError(f"{label} is not valid JSON") from exc


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    """Require one object with an exact public key set."""

    if not isinstance(value, dict) or set(value) != keys:
        raise MeshshotError(f"{label} has an invalid schema")
    return value


def _load_browser_authority() -> dict[str, Any] | None:
    """Return the fixed formal authority, or None outside a formal job."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(_AUTHORITY_PATH, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MeshshotError("formal browser authority file is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o444
        ):
            raise MeshshotError("formal browser authority file is replaceable")
        raw = os.read(descriptor, _MAX_AUTHORITY_BYTES + 1)
    except MeshshotError:
        raise
    except OSError as exc:
        raise MeshshotError("formal browser authority is unavailable") from exc
    finally:
        os.close(descriptor)
    if not raw or len(raw) > _MAX_AUTHORITY_BYTES:
        raise MeshshotError("formal browser authority has an invalid size")
    authority = _exact_object(
        _strict_json(raw, "formal browser authority"),
        {"schema", "jobId", "gateNonce", "imageId", "programs"},
        "formal browser authority",
    )
    if (
        authority["schema"] != _AUTHORITY_SCHEMA
        or not isinstance(authority["jobId"], str)
        or _JOB_ID.fullmatch(authority["jobId"]) is None
        or not isinstance(authority["gateNonce"], str)
        or _GATE_NONCE.fullmatch(authority["gateNonce"]) is None
        or authority["imageId"] != _SIDECAR_IMAGE_ID
        or authority["programs"] != _PROGRAMS
    ):
        raise MeshshotError("formal browser authority identity is invalid")
    return authority


def _registered_residual_render(
    authority: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Submit one exact residual request to the job-private program broker."""

    request = {
        "schema": _REQUEST_SCHEMA,
        "jobId": authority["jobId"],
        "imageId": authority["imageId"],
        "program": "residual",
        "payload": payload,
    }
    request_bytes = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if len(request_bytes) > _MAX_REQUEST_BYTES:
        raise MeshshotError(
            f"formal residual request exceeds {_MAX_REQUEST_BYTES} bytes"
        )
    response_bytes = bytearray()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_SOCKET_TIMEOUT_SECONDS)
            connection.connect(os.fspath(_SOCKET_PATH))
            connection.sendall(request_bytes + b"\n")
            connection.shutdown(socket.SHUT_WR)
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                response_bytes.extend(chunk)
                if len(response_bytes) > _MAX_RESPONSE_BYTES:
                    raise MeshshotError("formal residual response is too large")
    except MeshshotError:
        raise
    except OSError as exc:
        raise MeshshotError("formal Browser Sidecar request failed") from exc
    if not response_bytes.endswith(b"\n") or b"\n" in response_bytes[:-1]:
        raise MeshshotError("formal residual response framing is invalid")
    response = _exact_object(
        _strict_json(bytes(response_bytes[:-1]), "formal residual response"),
        {"schema", "jobId", "imageId", "program", "result"},
        "formal residual response",
    )
    if (
        response["schema"] != _RESPONSE_SCHEMA
        or response["jobId"] != authority["jobId"]
        or response["imageId"] != authority["imageId"]
        or response["program"] != "residual"
    ):
        raise MeshshotError("formal residual response identity is invalid")
    return _exact_object(
        response["result"],
        {"ok", "pngDataUrl", "views"},
        "formal residual result",
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

    loaded = load_profile()
    if variant not in loaded.profile["variants"]:
        raise MeshshotError(f"unsupported render variant: {variant}")
    directions = tuple(str(value) for value in exterior_directions)
    if len(set(directions)) != len(directions) or any(
        value not in _OUTSIDE_DIRECTIONS for value in directions
    ):
        raise MeshshotError("exterior directions must be unique signed x/y/z values")
    payload = {
        "profile": loaded.profile,
        "variant": variant,
        "reference": reference.to_json(),
        "candidate": candidate.to_json(),
        "exteriorDirections": list(directions),
    }
    authority = _load_browser_authority()
    if authority is None:
        result = _legacy_browser_render(payload)
    else:
        result = _registered_residual_render(
            authority,
            {
                "reference": payload["reference"],
                "candidate": payload["candidate"],
                "variant": variant,
                "exteriorDirections": list(directions),
                "options": {
                    "cameraPolicy": "profile-fixed",
                    "canonicalPostprocess": True,
                },
            },
        )

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
