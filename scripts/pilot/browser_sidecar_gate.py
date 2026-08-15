#!/usr/bin/env python3
"""Run the fixed Browser Gate, then exec the already-selected pilot workload."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
from typing import Any, Mapping, Sequence
from urllib.request import urlopen


GATE_ROOT = Path("/run/meshshot-gate")
sys.path.insert(0, os.fspath(GATE_ROOT / "meshshot-src"))

from PIL import Image  # noqa: E402
from meshshot import MeshGeometry, render_residual_preview  # noqa: E402


CONTRACT = json.loads(
    (GATE_ROOT / "meshshot-src/meshshot/browser_contract.json").read_text(
        encoding="utf-8"
    )
)
GATE = CONTRACT["nestedGate"]
AUTHORITY_PATH = Path(CONTRACT["authorityPath"])
BROKER_SOCKET_PATH = Path(CONTRACT["socketPath"])
GATE_SOCKET_PATH = Path(GATE["socketPath"])
MAX_PROOF_BYTES = GATE["maxProofBytes"]
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SOURCE_ALIASES = (Path("/repo"), Path("/src"), Path("/source"), Path("/workspaces"))
BROWSER_EXECUTABLES = (
    "/ms-playwright",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/opt/google/chrome",
)
BROWSER_CACHES = (
    "/home/pilot/.cache/ms-playwright",
    "/root/.cache/ms-playwright",
    "/tmp/ms-playwright",
)


def _strict_json(raw: bytes, label: str) -> Any:
    """Decode one duplicate-free JSON value."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _authority() -> Mapping[str, Any]:
    """Read the fixed outer-published authority used by both gate programs."""

    payload = _strict_json(AUTHORITY_PATH.read_bytes(), "authority")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "jobId", "imageId", "programs"}
        or payload.get("schema") != CONTRACT["authoritySchema"]
        or payload.get("imageId") != CONTRACT["sidecarImageId"]
        or payload.get("programs") != CONTRACT["programs"]
        or not isinstance(payload.get("jobId"), str)
    ):
        raise ValueError("fixed authority identity mismatch")
    return payload


def _viewer_request() -> Mapping[str, Any]:
    """Execute the one registered Viewer projection operation."""

    authority = _authority()
    request = {
        "schema": CONTRACT["requestSchema"],
        "jobId": authority["jobId"],
        "imageId": CONTRACT["sidecarImageId"],
        "program": "viewer",
        "payload": {
            "modelKey": "inspection-step",
            "inspectionControl": "toggle-projection",
        },
    }
    wire = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("ascii")
    response = bytearray()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(120)
        connection.connect(os.fspath(BROKER_SOCKET_PATH))
        connection.sendall(wire + b"\n")
        connection.shutdown(socket.SHUT_WR)
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > MAX_RESPONSE_BYTES:
                raise ValueError("Viewer response exceeded its bound")
    if not response.endswith(b"\n") or b"\n" in response[:-1]:
        raise ValueError("Viewer response framing is invalid")
    decoded = _strict_json(bytes(response[:-1]), "Viewer response")
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"schema", "jobId", "imageId", "program", "result"}
        or decoded.get("schema") != CONTRACT["responseSchema"]
        or decoded.get("jobId") != authority["jobId"]
        or decoded.get("imageId") != CONTRACT["sidecarImageId"]
        or decoded.get("program") != "viewer"
    ):
        raise ValueError("Viewer response identity mismatch")
    result = decoded.get("result")
    inspection = result.get("inspection") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or set(result)
        != {
            "title",
            "modelKey",
            "programDigest",
            "screenshotDataUrl",
            "screenshotSha256",
            "screenshotBytes",
            "bodyMentionsFixture",
            "bodyHasArtifactError",
            "inspection",
        }
        or result.get("modelKey") != "inspection-step"
        or result.get("programDigest") != CONTRACT["programs"]["viewer"]
        or result.get("bodyMentionsFixture") is not True
        or result.get("bodyHasArtifactError") is not False
        or not isinstance(inspection, dict)
        or inspection.get("before")
        != "Display and projection: Solid, Orthographic"
        or inspection.get("after") != "Display and projection: Solid, Perspective"
        or inspection.get("changed") is not True
    ):
        raise ValueError("Viewer projection predicate failed")
    return result


def _browser_processes() -> list[str]:
    """Inventory browser processes in the gate/Agent PID namespace."""

    found: list[str] = []
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            command = (process_dir / "cmdline").read_bytes().replace(b"\0", b" ")
            name = (process_dir / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        text = command.decode("utf-8", errors="replace")
        if re.search(r"(?:chromium|chrome)(?:\s|$)", f"{name} {text}", re.I):
            found.append(name)
    return found


def run_gate_checks() -> Mapping[str, Any]:
    """Run both registered programs and inspect the future Agent namespace."""

    reference = MeshGeometry(
        vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    candidate = MeshGeometry(
        vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    rendered = render_residual_preview(
        reference,
        candidate,
        variant="step",
        exterior_directions=[],
    )
    png_sha256 = hashlib.sha256(rendered.png_bytes).hexdigest()
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        image.load()
        mode = image.mode
        size = list(image.size)
    views = [view["name"] for view in rendered.views]
    residual = {
        "pngSha256": png_sha256,
        "mode": mode,
        "size": size,
        "profileSha256": rendered.profile_sha256,
        "views": views,
    }
    if residual != {
        "pngSha256": GATE["publicPngSha256"],
        "mode": "RGB",
        "size": [504, 1008],
        "profileSha256": GATE["profileSha256"],
        "views": GATE["views"],
    }:
        raise ValueError("public residual parity predicate failed")
    viewer_result = _viewer_request()
    viewer = {
        "before": viewer_result["inspection"]["before"],
        "after": viewer_result["inspection"]["after"],
        "bodyMentionsFixture": viewer_result["bodyMentionsFixture"],
        "bodyHasArtifactError": viewer_result["bodyHasArtifactError"],
    }
    executables = sorted(
        {
            path
            for path in BROWSER_EXECUTABLES
            if Path(path).exists()
        }
        | {
            path
            for name in ("chromium", "chromium-browser", "google-chrome", "chrome")
            if (path := shutil.which(name)) is not None
        }
    )
    caches = [path for path in BROWSER_CACHES if Path(path).exists()]
    processes = _browser_processes()
    aliases = [os.fspath(path) for path in SOURCE_ALIASES if path.exists()]
    try:
        with urlopen("https://example.com/", timeout=3) as response:
            response.read(1)
    except Exception:
        egress_blocked = True
    else:
        egress_blocked = False
    inventory = {
        "browserExecutables": executables,
        "browserCaches": caches,
        "browserProcesses": processes,
        "sourceAliases": aliases,
    }
    predicates = {
        "publicResidualParity": True,
        "viewerProjectionChanged": True,
        "viewerArtifactClean": True,
        "browserInventoryEmpty": not executables and not caches,
        "browserProcessZero": not processes,
        "sourceHidden": not aliases,
        "egressBlocked": egress_blocked,
    }
    if any(value is not True for value in predicates.values()):
        raise ValueError("nested namespace predicate failed")
    return {
        "schema": GATE["schema"],
        "status": "succeeded",
        "predicates": predicates,
        "residual": residual,
        "viewer": viewer,
        "inventory": inventory,
    }


def _failed_proof() -> Mapping[str, Any]:
    """Return a fixed closed proof without exposing an exception string."""

    return {
        "schema": GATE["schema"],
        "status": "failed",
        "predicates": {name: False for name in GATE["predicates"]},
        "residual": {
            "pngSha256": "0" * 64,
            "mode": "",
            "size": [0, 0],
            "profileSha256": "0" * 64,
            "views": [],
        },
        "viewer": {
            "before": "",
            "after": "",
            "bodyMentionsFixture": False,
            "bodyHasArtifactError": True,
        },
        "inventory": {
            "browserExecutables": [],
            "browserCaches": [],
            "browserProcesses": [],
            "sourceAliases": [],
        },
    }


def publish_and_wait(proof: Mapping[str, Any]) -> bool:
    """Publish one proof and wait for the outer one-byte exec release."""

    wire = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("ascii")
    if not wire or len(wire) + 1 > MAX_PROOF_BYTES:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(180)
            connection.connect(os.fspath(GATE_SOCKET_PATH))
            connection.sendall(wire + b"\n")
            connection.shutdown(socket.SHUT_WR)
            release = connection.recv(2)
            if release != b"\x01" or connection.recv(1) != b"":
                return False
    except OSError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate with no variable inputs, then replace this exact PID."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "--" or len(arguments) == 1:
        return 2
    workload = arguments[1:]
    try:
        proof = run_gate_checks()
    except Exception:
        publish_and_wait(_failed_proof())
        return 1
    if not publish_and_wait(proof):
        return 1
    os.execvpe(workload[0], workload, os.environ.copy())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
