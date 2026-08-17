#!/usr/bin/env python3
"""Exercise the sealed gate and packaged conformance client inside one test container."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading

from scripts.pilot import browser_sidecar


CAPABILITY = Path("/run/meshshot-browser")
FIXTURE = Path("/tmp/browser-sidecar-image-fixture.json")
CLIENT = "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py"


def _read_request(connection: socket.socket) -> dict[str, object]:
    wire = bytearray()
    while chunk := connection.recv(65536):
        wire.extend(chunk)
    value = json.loads(bytes(wire).decode("ascii"))
    if not isinstance(value, dict):
        raise ValueError("registered request is malformed")
    return value


def _viewer_result() -> dict[str, object]:
    return {
        "title": "CAD Viewer | browser_sidecar_inspection.step",
        "modelKey": "inspection-step",
        "programDigest": browser_sidecar.PROGRAMS["viewer"],
        "screenshotDataUrl": "data:image/png;base64,cG5n",
        "screenshotSha256": "0" * 64,
        "screenshotBytes": 3,
        "bodyMentionsFixture": True,
        "bodyHasArtifactError": False,
        "inspection": {
            "control": "toggle-projection",
            "before": "Display and projection: Solid, Orthographic",
            "target": "Perspective",
            "after": "Display and projection: Solid, Perspective",
            "changed": True,
        },
    }


def _serve_registered_programs(
    listener: socket.socket,
    fixture: dict[str, object],
    requests: list[str],
    errors: list[BaseException],
) -> None:
    try:
        for _ in range(4):
            connection, _ = listener.accept()
            with connection:
                request = _read_request(connection)
                program = request.get("program")
                if program not in {"residual", "viewer"}:
                    raise ValueError("unregistered program")
                requests.append(program)
                result = (
                    {
                        "ok": True,
                        "pngDataUrl": fixture["pngDataUrl"],
                        "views": fixture["views"],
                    }
                    if program == "residual"
                    else _viewer_result()
                )
                response = {
                    "schema": browser_sidecar.RESPONSE_SCHEMA,
                    "jobId": request["jobId"],
                    "imageId": browser_sidecar.IMAGE_ID,
                    "program": program,
                    "result": result,
                }
                connection.sendall(
                    json.dumps(response, sort_keys=True, separators=(",", ":")).encode(
                        "ascii"
                    )
                    + b"\n"
                )
    except BaseException as exc:
        errors.append(exc)


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="ascii"))
    gate_input = json.loads(
        (CAPABILITY / "gate-input.json").read_text(encoding="ascii")
    )
    surface_sha256 = hashlib.sha256(
        json.dumps(
            gate_input["surfaceManifest"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    broker_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    broker_listener.bind(os.fspath(CAPABILITY / "broker" / "browser.sock"))
    broker_listener.listen(4)
    gate_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    gate_listener.bind(os.fspath(CAPABILITY / "gate.sock"))
    gate_listener.listen(1)
    requests: list[str] = []
    errors: list[BaseException] = []
    broker_thread = threading.Thread(
        target=_serve_registered_programs,
        args=(broker_listener, fixture, requests, errors),
        daemon=True,
    )
    broker_thread.start()
    process = subprocess.Popen(
        [
            "python3",
            os.fspath(CAPABILITY / "browser-gate.pyz"),
            "--",
            CLIENT,
            "client",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proof_connection, _ = gate_listener.accept()
    with proof_connection:
        proof = _read_request(proof_connection)
        if proof.get("status") == "succeeded":
            browser_sidecar.validate_nested_gate_proof(
                proof,
                expected_job_id=gate_input["jobId"],
                expected_nonce=gate_input["nonce"],
                expected_artifact_sha256=gate_input["artifactSha256"],
                expected_surface_manifest_sha256=surface_sha256,
            )
        proof_connection.sendall(b"\x01")
    stdout, stderr = process.communicate(timeout=180)
    broker_thread.join(timeout=5)
    broker_listener.close()
    gate_listener.close()
    if process.returncode != 0 or errors or broker_thread.is_alive():
        raise RuntimeError(
            "packaged client failed: "
            f"{process.returncode}: proof={proof}: requests={requests}: "
            f"broker={errors}: {stderr}"
        )
    if requests != ["residual", "viewer", "residual", "viewer"]:
        raise RuntimeError("registered request sequence mismatch")
    lines = stdout.splitlines()
    if len(lines) != 1:
        raise RuntimeError("packaged client output is malformed")
    print(lines[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
