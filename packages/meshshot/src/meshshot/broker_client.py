"""Browser-free client for the registered residual rendering program.

This module is the authoritative Agent-side implementation.  It deliberately
contains no local-browser fallback: a caller without the fixed, outer-owned
authority file fails closed.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
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


_CONTRACT = json.loads(
    files("meshshot").joinpath("browser_contract.json").read_text(encoding="utf-8")
)
AUTHORITY_PATH = Path(_CONTRACT["authorityPath"])
SOCKET_PATH = Path(_CONTRACT["socketPath"])
_AUTHORITY_SCHEMA = _CONTRACT["authoritySchema"]
_REQUEST_SCHEMA = _CONTRACT["requestSchema"]
_RESPONSE_SCHEMA = _CONTRACT["responseSchema"]
_SIDECAR_IMAGE_ID = _CONTRACT["sidecarImageId"]
_PROGRAMS = _CONTRACT["programs"]
_JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
_GATE_NONCE = re.compile(r"[0-9a-f]{16,64}\Z")
_MAX_AUTHORITY_BYTES = 16 * 1024
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_SOCKET_TIMEOUT_SECONDS = 120.0
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


def _strict_json(payload: bytes, label: str) -> Any:
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
    if not isinstance(value, dict) or set(value) != keys:
        raise MeshshotError(f"{label} has an invalid schema")
    return value


def load_browser_authority(
    authority_path: Path = AUTHORITY_PATH,
    *,
    open_file: Callable[[Path, int], int] = os.open,
    fstat_file: Callable[[int], Any] = os.fstat,
    read_file: Callable[[int, int], bytes] = os.read,
    close_file: Callable[[int], None] = os.close,
    effective_uid: Callable[[], int] = os.getuid,
) -> dict[str, Any] | None:
    """Load the fixed authority without following or accepting replaceable files."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = open_file(authority_path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MeshshotError("formal browser authority file is unavailable") from exc
    try:
        metadata = fstat_file(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != effective_uid()
            or stat.S_IMODE(metadata.st_mode) != 0o444
        ):
            raise MeshshotError("formal browser authority file is replaceable")
        raw = read_file(descriptor, _MAX_AUTHORITY_BYTES + 1)
    except MeshshotError:
        raise
    except OSError as exc:
        raise MeshshotError("formal browser authority is unavailable") from exc
    finally:
        close_file(descriptor)
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


def registered_residual_render(
    authority: dict[str, Any],
    payload: dict[str, Any],
    *,
    socket_path: Path = SOCKET_PATH,
    socket_factory: Callable[..., Any] = socket.socket,
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
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(request_bytes) > _MAX_REQUEST_BYTES:
        raise MeshshotError("formal residual request exceeds 1 MiB")
    response_bytes = bytearray()
    try:
        with socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_SOCKET_TIMEOUT_SECONDS)
            connection.connect(os.fspath(socket_path))
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
        response["result"], {"ok", "pngDataUrl", "views"}, "formal residual result"
    )


def prepare_payload(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    variant: str,
    exterior_directions: Sequence[str],
) -> tuple[Any, dict[str, Any], tuple[str, ...]]:
    loaded = load_profile()
    if variant not in loaded.profile["variants"]:
        raise MeshshotError(f"unsupported render variant: {variant}")
    directions = tuple(str(value) for value in exterior_directions)
    if len(set(directions)) != len(directions) or any(
        value not in _OUTSIDE_DIRECTIONS for value in directions
    ):
        raise MeshshotError("exterior directions must be unique signed x/y/z values")
    return loaded, {
        "profile": loaded.profile,
        "variant": variant,
        "reference": reference.to_json(),
        "candidate": candidate.to_json(),
        "exteriorDirections": list(directions),
    }, directions


def broker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference": payload["reference"],
        "candidate": payload["candidate"],
        "variant": payload["variant"],
        "exteriorDirections": payload["exteriorDirections"],
        "options": {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True},
    }


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
    """Render through the fixed Broker authority; never start a local browser."""

    loaded, payload, _ = prepare_payload(reference, candidate, variant, exterior_directions)
    authority = load_browser_authority()
    if authority is None:
        raise MeshshotError("formal browser authority file is required")
    result = registered_residual_render(authority, broker_payload(payload))
    return finalize_preview(result, loaded, variant)
