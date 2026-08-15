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

from PIL import Image

from meshshot import MeshGeometry, render_residual_preview
from scripts.pilot import browser_sidecar
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
from scripts.pilot.browser_gate_contract import (
    CONFORMANCE_OPTIONAL_ROOTS,
    CONFORMANCE_REQUIRED_ROOTS,
    CONFORMANCE_SURFACE_SCHEMA,
    CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS,
)
from scripts.pilot.runner import (
    NestedGateChannel,
    _build_gate_artifact,
    _prepare_nested_browser_gate_from_manifest,
)


AUTHORITY_PATH = SANDBOX_AUTHORITY_PATH
SOCKET_PATH = SANDBOX_SOCKET_PATH
EXPECTED_PUBLIC_PNG_SHA256 = (
    "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b"
)
VIEW_ORDER = ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
CONFORMANCE_TIMEOUT_SECONDS = 180


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
        or set(payload) != {"schema", "jobId", "gateNonce", "imageId", "programs"}
        or payload.get("schema") != AUTHORITY_SCHEMA
        or payload.get("imageId") != IMAGE_ID
        or payload.get("programs") != PROGRAMS
        or not isinstance(payload.get("gateNonce"), str)
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
    browser_processes = _browser_processes()
    if browser_executables or browser_processes:
        raise ValueError("conformance client browser predicate failed")
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
        "clientBrowserInventory": {
            "browserExecutablesVisible": browser_executables,
            "browserProcesses": browser_processes,
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


def _fixed_container_isolation() -> list[str]:
    """Return the identical fixed isolation shared by discovery and execution."""

    return [
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
        "--tmpfs",
        (
            "/home/pwuser:rw,nosuid,nodev,size=16m,"
            f"uid={os.getuid()},gid={os.getgid()},mode=700"
        ),
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]


def _discover_client_surface(
    docker: str,
    job: BrowserSidecarJob,
    artifact: Path,
    container_name: str,
) -> Mapping[str, object]:
    """Run the sealed fixed discovery role in the exact client image."""

    absent = subprocess.run(
        [docker, "container", "inspect", container_name],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    if absent.returncode != 1:
        raise RuntimeError("foreign conformance-surface name exists")
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            job.label,
            "--label",
            job.owner_label,
            *_fixed_container_isolation(),
            "--mount",
            (
                f"type=bind,src={artifact},"
                f"dst={browser_sidecar.NESTED_GATE['artifactPath']},readonly"
            ),
            "--entrypoint",
            "python3",
            BROKER_IMAGE_ID,
            browser_sidecar.NESTED_GATE["artifactPath"],
            "--discover-conformance-surface",
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=CONFORMANCE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError("fixed conformance-surface discovery failed")
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise RuntimeError("fixed conformance-surface result is malformed")
    decoded = _strict_json(lines[0].encode("ascii"), "conformance surface")
    allowed_roots = {
        *CONFORMANCE_REQUIRED_ROOTS,
        *CONFORMANCE_OPTIONAL_ROOTS,
        *CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS,
    }
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"schema", "scanRoots", "browserExclusions"}
        or decoded.get("schema") != CONFORMANCE_SURFACE_SCHEMA
        or not isinstance(decoded.get("scanRoots"), list)
        or decoded["scanRoots"] != sorted(set(decoded["scanRoots"]))
        or not set(CONFORMANCE_REQUIRED_ROOTS).issubset(decoded["scanRoots"])
        or not set(decoded["scanRoots"]).issubset(allowed_roots)
    ):
        raise RuntimeError("fixed conformance-surface result is malformed")
    return {
        "schema": browser_sidecar.NESTED_GATE["surfaceSchema"],
        "scanRoots": decoded["scanRoots"],
        "browserExclusions": decoded["browserExclusions"],
    }


def _browser_mask_arguments(manifest: Mapping[str, object]) -> list[str]:
    """Translate the already-validated canonical surface into Docker masks."""

    arguments: list[str] = []
    exclusions = manifest["browserExclusions"]
    assert isinstance(exclusions, list)
    for exclusion in exclusions:
        assert isinstance(exclusion, dict)
        if exclusion["mask"] == "tmpfs":
            arguments.extend(
                ["--tmpfs", f"{exclusion['target']}:ro,nosuid,nodev,size=1m"]
            )
        else:
            arguments.extend(
                [
                    "--mount",
                    (
                        "type=bind,src=/dev/null,"
                        f"dst={exclusion['target']},readonly"
                    ),
                ]
            )
    return arguments


def _run_gate_then_client(
    docker: str,
    job: BrowserSidecarJob,
    client_name: str,
    manifest: Mapping[str, object],
) -> tuple[int, Mapping[str, Any]]:
    """Validate one proof before releasing the fixed Agent-equivalent client."""

    channel = NestedGateChannel(job.capability_dir)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
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
                *_fixed_container_isolation(),
                *_browser_mask_arguments(manifest),
                "--mount",
                (
                    "type=bind,src="
                    f"{job.capability_dir},dst=/run/meshshot-browser,readonly"
                ),
                "--entrypoint",
                "python3",
                BROKER_IMAGE_ID,
                browser_sidecar.NESTED_GATE["artifactPath"],
                "--",
                "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py",
                "client",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        proof = channel.receive(lambda: False)
        job.record_nested_gate(proof)
        channel.release()
        stdout, _ = process.communicate(timeout=CONFORMANCE_TIMEOUT_SECONDS)
        status = process.returncode
        if status != 0:
            raise RuntimeError("fixed conformance client failed")
        lines = stdout.splitlines()
        if len(lines) != 1:
            raise RuntimeError("fixed conformance client output is malformed")
        decoded = _strict_json(lines[0].encode("ascii"), "conformance client")
        if not isinstance(decoded, dict):
            raise RuntimeError("fixed conformance client result is malformed")
        return status, decoded
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        raise
    finally:
        channel.close()


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
        surface_name = f"{job.prefix}-surface"
        client_result: Mapping[str, Any] | None = None
        client_status: int | None = None
        error: str | None = None
        try:
            discovery_artifact = Path(temporary) / "browser-gate-discovery.pyz"
            _build_gate_artifact(browser_sidecar.REPO_ROOT, discovery_artifact)
            manifest = _discover_client_surface(
                docker, job, discovery_artifact, surface_name
            )
            _prepare_nested_browser_gate_from_manifest(
                browser_sidecar.REPO_ROOT, job, manifest
            )
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
            client_status, client_result = _run_gate_then_client(
                docker, job, client_name, manifest
            )
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            client_status = 1 if client_status is None else client_status
        finally:
            try:
                for name in (client_name, surface_name):
                    subprocess.run(
                        [docker, "rm", "-f", name],
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
