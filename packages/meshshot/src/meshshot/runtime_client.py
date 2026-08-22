"""Agent-side client for the one Browser Runtime residual operation."""

from __future__ import annotations

import base64
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

from PIL import Image

from meshshot.profile import load_profile


RUNTIME_CAPABILITY_PATH = Path("/run/meshshot-browser/runtime.json")
RUNTIME_CAPABILITY_SCHEMA = "text-to-cad.browser-runtime-capability/1"
RUNTIME_REQUEST_SCHEMA = "text-to-cad.cad-render-request/2"
RUNTIME_RESPONSE_SCHEMA = "text-to-cad.cad-render-response/1"
EXPECTED_RESIDUAL_PROGRAM = (
    "sha256:9a7fbaf17a65f8e44c116833eee1b30cf023a50f2c52b30ced030203fe255d33"
)
EXPECTED_SNAPSHOT_PROGRAM = (
    "sha256:a9fb496cd605bf454bbb2e539238fa302b71b1989117e24ffda0b331e2752f61"
)
_HEX_12_TO_64 = re.compile(r"[0-9a-f]{12,64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OUTSIDE_DIRECTIONS = frozenset({"-x", "+x", "-y", "+y", "-z", "+z"})
_MAX_FINITE_FLOAT32 = 3.4028234663852886e38
_MAX_CAPABILITY_BYTES = 16 * 1024
_MAX_REQUEST_BYTES = 96 * 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_TIMEOUT_SECONDS = 120.0
_LOOPBACK_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))


class MeshshotError(RuntimeError):
    """Stable residual-preview failure."""


@dataclass(frozen=True)
class MeshGeometry:
    """JSON-safe indexed triangles in the canonical renderer frame."""

    vertices: Sequence[Sequence[float]]
    faces: Sequence[Sequence[int]]

    def to_packed_json(self) -> dict[str, Any]:
        """Encode one mesh without expanding every scalar into JSON objects."""

        vertex_count = len(self.vertices)
        face_count = len(self.faces)
        if vertex_count == 0:
            raise MeshshotError(
                "mesh geometry requires finite three-dimensional vertices"
            )
        positions = array("f")
        for vertex in self.vertices:
            if len(vertex) != 3:
                raise MeshshotError(
                    "mesh geometry requires finite three-dimensional vertices"
                )
            values = tuple(float(value) for value in vertex)
            if not all(
                math.isfinite(value) and abs(value) <= _MAX_FINITE_FLOAT32
                for value in values
            ):
                raise MeshshotError(
                    "mesh geometry requires finite three-dimensional vertices"
                )
            positions.extend(values)

        if face_count == 0:
            raise MeshshotError("mesh geometry requires valid triangle indices")
        indices = array("I")
        if indices.itemsize != 4:
            raise MeshshotError("mesh geometry requires 32-bit triangle indices")
        for face in self.faces:
            if len(face) != 3:
                raise MeshshotError("mesh geometry requires valid triangle indices")
            values = tuple(int(value) for value in face)
            if any(index < 0 or index >= vertex_count for index in values):
                raise MeshshotError("mesh geometry requires valid triangle indices")
            indices.extend(values)

        if sys.byteorder != "little":
            positions.byteswap()
            indices.byteswap()
        return {
            "schema": "text-to-cad.packed-triangle-mesh/1",
            "vertexCount": vertex_count,
            "faceCount": face_count,
            "positionsF32LeBase64": base64.b64encode(positions).decode("ascii"),
            "indicesU32LeBase64": base64.b64encode(indices).decode("ascii"),
        }


@dataclass(frozen=True)
class RenderedPreview:
    """Opaque RGB PNG bytes and semantic facts returned by Browser Runtime."""

    png_bytes: bytes
    variant: str
    profile_sha256: str
    views: tuple[dict[str, Any], ...]


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


def load_runtime_capability(
    capability_path: Path | None = None,
) -> dict[str, Any]:
    """Load the required job-owned Browser Runtime capability."""

    capability_path = capability_path or RUNTIME_CAPABILITY_PATH
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(capability_path, flags)
    except FileNotFoundError as exc:
        raise MeshshotError("browser runtime capability is required") from exc
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
        raw = os.read(descriptor, _MAX_CAPABILITY_BYTES + 1)
    except MeshshotError:
        raise
    except OSError as exc:
        raise MeshshotError("browser runtime capability is unavailable") from exc
    finally:
        os.close(descriptor)
    if not raw or len(raw) > _MAX_CAPABILITY_BYTES:
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
    if (
        capability["schema"] != RUNTIME_CAPABILITY_SCHEMA
        or not isinstance(capability["jobId"], str)
        or _HEX_12_TO_64.fullmatch(capability["jobId"]) is None
        or not isinstance(capability["imageRef"], str)
        or _SHA256.fullmatch(capability["imageRef"]) is None
        or not _valid_loopback_url(capability["mcpUrl"], "/mcp")
        or not _valid_loopback_url(
            capability["cadRenderUrl"], "/cad/render/residual"
        )
        or not isinstance(capability["cadRenderToken"], str)
        or _HEX_12_TO_64.fullmatch(capability["cadRenderToken"]) is None
        or not isinstance(programs, dict)
        or programs != {
            "residual": EXPECTED_RESIDUAL_PROGRAM,
            "snapshot": EXPECTED_SNAPSHOT_PROGRAM,
        }
    ):
        raise MeshshotError("browser runtime capability identity is invalid")
    return capability


def prepare_payload(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    variant: str,
    exterior_directions: Sequence[str],
) -> tuple[Any, dict[str, Any]]:
    loaded = load_profile()
    if variant not in loaded.profile["variants"]:
        raise MeshshotError(f"unsupported render variant: {variant}")
    directions = tuple(str(value) for value in exterior_directions)
    if len(set(directions)) != len(directions) or any(
        value not in _OUTSIDE_DIRECTIONS for value in directions
    ):
        raise MeshshotError("exterior directions must be unique signed x/y/z values")
    payload = {
        "reference": reference.to_packed_json(),
        "candidate": candidate.to_packed_json(),
        "variant": variant,
        "exteriorDirections": list(directions),
        "options": {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True},
    }
    return loaded, payload


def render_with_runtime(
    capability: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    request_value = {
        "schema": RUNTIME_REQUEST_SCHEMA,
        "jobId": capability["jobId"],
        "program": "residual",
        "programDigest": capability["programs"]["residual"],
        "payload": payload,
    }
    request_bytes = json.dumps(
        request_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if len(request_bytes) > _MAX_REQUEST_BYTES:
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
        with _LOOPBACK_OPENER.open(request, timeout=_TIMEOUT_SECONDS) as response:
            response_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib_error.URLError) as exc:
        raise MeshshotError("browser runtime residual request failed") from exc
    if len(response_bytes) > _MAX_RESPONSE_BYTES:
        raise MeshshotError("browser runtime residual response is too large")
    try:
        response_value = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeshshotError("browser runtime residual response is invalid") from exc
    if (
        not isinstance(response_value, dict)
        or set(response_value)
        != {"schema", "jobId", "program", "programDigest", "result"}
        or response_value["schema"] != RUNTIME_RESPONSE_SCHEMA
        or response_value["jobId"] != capability["jobId"]
        or response_value["program"] != "residual"
        or response_value["programDigest"] != capability["programs"]["residual"]
        or not isinstance(response_value["result"], dict)
    ):
        raise MeshshotError("browser runtime residual response identity is invalid")
    return response_value["result"]


def finalize_preview(result: Any, loaded: Any, variant: str) -> RenderedPreview:
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


def render_residual_preview(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    *,
    variant: str = "step",
    exterior_directions: Sequence[str] = (),
) -> RenderedPreview:
    """Render through the required job-owned Browser Runtime."""

    loaded, payload = prepare_payload(
        reference, candidate, variant, exterior_directions
    )
    capability = load_runtime_capability()
    result = render_with_runtime(capability, payload)
    return finalize_preview(result, loaded, variant)
