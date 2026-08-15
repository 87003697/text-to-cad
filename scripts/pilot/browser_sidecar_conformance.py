#!/usr/bin/env python3
"""Run one fixed production-shaped Browser Sidecar conformance job."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.request import urlopen

from PIL import Image

from meshshot import MeshGeometry, render_residual_preview
from scripts.pilot.browser_sidecar import (
    AUTHORITY_SCHEMA,
    BROKER_IMAGE_ID,
    BrowserSidecarJob,
    IMAGE_ID,
    PROGRAMS,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    SANDBOX_AUTHORITY_PATH,
    SANDBOX_SOCKET_PATH,
)


AUTHORITY_PATH = SANDBOX_AUTHORITY_PATH
SOCKET_PATH = SANDBOX_SOCKET_PATH
EXPECTED_PUBLIC_PNG_SHA256 = (
    "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b"
)
VIEW_ORDER = ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


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
    """Read the fixed read-only job authority used by the nested client."""

    payload = _strict_json(AUTHORITY_PATH.read_bytes(), "authority")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "jobId", "imageId", "programs"}
        or payload.get("schema") != AUTHORITY_SCHEMA
        or payload.get("imageId") != IMAGE_ID
        or payload.get("programs") != PROGRAMS
    ):
        raise ValueError("formal authority identity mismatch")
    return payload


def _request(program: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Send one exact registered request over the fixed private socket."""

    authority = _authority()
    request = {
        "schema": REQUEST_SCHEMA,
        "jobId": authority["jobId"],
        "imageId": IMAGE_ID,
        "program": program,
        "payload": payload,
    }
    wire = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("ascii")
    response = bytearray()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(120)
        connection.connect(os.fspath(SOCKET_PATH))
        connection.sendall(wire + b"\n")
        connection.shutdown(socket.SHUT_WR)
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > MAX_RESPONSE_BYTES:
                raise ValueError("registered response exceeded its bound")
    if not response.endswith(b"\n") or b"\n" in response[:-1]:
        raise ValueError("registered response framing is invalid")
    decoded = _strict_json(bytes(response[:-1]), "registered response")
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"schema", "jobId", "imageId", "program", "result"}
        or decoded.get("schema") != RESPONSE_SCHEMA
        or decoded.get("jobId") != authority["jobId"]
        or decoded.get("imageId") != IMAGE_ID
        or decoded.get("program") != program
    ):
        raise ValueError("registered response identity mismatch")
    return decoded


def _browser_processes() -> list[dict[str, object]]:
    """Inventory browser-like processes in this nested PID namespace."""

    found: list[dict[str, object]] = []
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            command = (process_dir / "cmdline").read_bytes().replace(b"\0", b" ")
            name = (process_dir / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        text = command.decode("utf-8", errors="replace")
        if re.search(r"(?:chromium|chrome)(?:\s|$)", f"{name} {text}", re.I):
            found.append({"pid": int(process_dir.name), "name": name})
    return found


def validate_viewer_result(viewer: Mapping[str, Any]) -> Mapping[str, Any]:
    """Require the actual registered Viewer response used by conformance."""

    viewer_result = viewer.get("result")
    inspection = viewer_result.get("inspection") if isinstance(viewer_result, dict) else None
    if (
        not isinstance(viewer_result, dict)
        or set(viewer_result)
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
        or viewer_result.get("modelKey") != "inspection-step"
        or viewer_result.get("programDigest") != PROGRAMS["viewer"]
        or viewer_result.get("bodyMentionsFixture") is not True
        or viewer_result.get("bodyHasArtifactError") is not False
        or not isinstance(inspection, dict)
        or inspection.get("before") != "Display and projection: Solid, Orthographic"
        or inspection.get("after") != "Display and projection: Solid, Perspective"
        or inspection.get("changed") is not True
    ):
        raise ValueError("Viewer projection predicate failed")
    return viewer_result


def run_client() -> Mapping[str, Any]:
    """Exercise public residual and registered Viewer from a browser-less client."""

    if sys.argv[1:] != ["client"]:
        raise ValueError("conformance client accepts no variable arguments")
    geometry = {
        "reference": {
            "vertices": [[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
            "faces": [[0, 1, 2]],
        },
        "candidate": {
            "vertices": [[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
            "faces": [[0, 1, 2]],
        },
    }
    rendered = render_residual_preview(
        MeshGeometry(**geometry["reference"]),
        MeshGeometry(**geometry["candidate"]),
        variant="step",
        exterior_directions=[],
    )
    png_sha256 = hashlib.sha256(rendered.png_bytes).hexdigest()
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        image.load()
        image_mode = image.mode
        image_size = list(image.size)
    if (
        type(rendered).__name__ != "RenderedPreview"
        or png_sha256 != EXPECTED_PUBLIC_PNG_SHA256
        or image_mode != "RGB"
        or image_size != [504, 1008]
        or tuple(view["name"] for view in rendered.views) != VIEW_ORDER
    ):
        raise ValueError("public residual parity predicate failed")
    viewer = _request(
        "viewer",
        {"modelKey": "inspection-step", "inspectionControl": "toggle-projection"},
    )
    viewer_result = validate_viewer_result(viewer)
    executable_candidates = (
        "/ms-playwright",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/opt/google/chrome",
    )
    browser_executables = [path for path in executable_candidates if Path(path).exists()]
    source_aliases = [
        path
        for path in ("/workspace", "/repo", "/src", "/source", "/workspaces")
        if Path(path).exists()
    ]
    try:
        with urlopen("https://example.com/", timeout=3) as response:
            response.read(1)
    except Exception:
        external_egress_blocked = True
    else:
        external_egress_blocked = False
    browser_processes = _browser_processes()
    if browser_executables or browser_processes or source_aliases or not external_egress_blocked:
        raise ValueError("nested isolation predicate failed")
    return {
        "schema": "meshshot.browser-sidecar.local-conformance-client/1",
        "publicResidual": {
            "callable": "meshshot.render_residual_preview",
            "renderedType": type(rendered).__name__,
            "pngBytes": len(rendered.png_bytes),
            "pngSha256": png_sha256,
            "imageMode": image_mode,
            "imageSize": image_size,
            "profileSha256": rendered.profile_sha256,
            "views": list(rendered.views),
        },
        "viewer": viewer_result,
        "nestedIsolation": {
            "browserExecutablesVisible": browser_executables,
            "browserProcesses": browser_processes,
            "sourceAliasesVisible": source_aliases,
            "externalEgressBlocked": external_egress_blocked,
        },
    }


def _write_atomic(path: Path, payload: object) -> None:
    """Publish one canonical conformance artifact atomically."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_host(evidence_path: Path) -> int:
    """Own one exact job and one fixed networkless conformance client."""

    docker = shutil.which("docker")
    if docker is None or not evidence_path.is_absolute():
        return 2
    with tempfile.TemporaryDirectory(prefix="meshshot-formal-conformance-") as temporary:
        job = BrowserSidecarJob(
            Path(temporary),
            Path("/workspace/repo/outputs/conformance/formal"),
            job_id="formal-local-conformance",
        )
        client_name = f"{job.prefix}-client"
        client_result: Mapping[str, Any] | None = None
        client_status: int | None = None
        error: str | None = None
        try:
            job.start()
            absent = subprocess.run(
                [docker, "container", "inspect", client_name],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            if absent.returncode != 1:
                raise RuntimeError("foreign conformance-client name exists")
            completed = subprocess.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "--name",
                    client_name,
                    "--label",
                    job.label,
                    "--label",
                    job.owner_label,
                    "--pull=never",
                    "--platform",
                    "linux/amd64",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    "32",
                    "--memory",
                    "256m",
                    "--memory-swap",
                    "256m",
                    "--cpus",
                    "0.5",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=16m,mode=1777",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--mount",
                    (
                        "type=bind,src="
                        f"{job.capability_dir},dst=/run/meshshot-browser,readonly"
                    ),
                    "--entrypoint",
                    "python3",
                    BROKER_IMAGE_ID,
                    "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py",
                    "client",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
            client_status = completed.returncode
            if client_status != 0:
                raise RuntimeError(f"fixed conformance client failed: {completed.stderr[:1000]}")
            lines = completed.stdout.splitlines()
            if len(lines) != 1:
                raise RuntimeError("fixed conformance client output is malformed")
            decoded = _strict_json(lines[0].encode("ascii"), "conformance client")
            if not isinstance(decoded, dict):
                raise RuntimeError("fixed conformance client result is malformed")
            client_result = decoded
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            client_status = 1 if client_status is None else client_status
        finally:
            try:
                subprocess.run(
                    [docker, "rm", "-f", client_name],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = f"client-cleanup: {type(exc).__name__}"
                client_status = 1
            receipt = job.close(workload_status=client_status)
        succeeded = client_status == 0 and receipt.get("status") == "succeeded"
        evidence = {
            "schema": "meshshot.browser-sidecar.local-conformance/1",
            "status": "succeeded" if succeeded else "failed",
            "error": error,
            "client": client_result,
            "receipt": receipt,
        }
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(evidence_path, evidence)
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return 0 if succeeded else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse only the fixed client or local-host conformance action."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("client")
    host = subparsers.add_parser("host")
    host.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fixed conformance role."""

    args = parse_args(argv)
    if args.action == "client":
        result = run_client()
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    return run_host(args.evidence)


if __name__ == "__main__":
    raise SystemExit(main())
