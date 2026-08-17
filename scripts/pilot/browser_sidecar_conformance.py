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
import stat
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
AUTHORITY_PATH = SANDBOX_AUTHORITY_PATH
SOCKET_PATH = SANDBOX_SOCKET_PATH
EXPECTED_PUBLIC_PNG_SHA256 = (
    "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b"
)
VIEW_ORDER = ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
CONFORMANCE_TIMEOUT_SECONDS = 180
DISCOVERY_ARTIFACT_PATH = "/opt/text-to-cad/scripts/pilot/browser_sidecar_gate.py"


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
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/opt/google/chrome",
    )
    browser_executables = [
        path
        for path in executable_candidates
        if Path(path).is_file() and os.access(path, os.X_OK)
    ]
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


def _publish_preflight_failure(
    evidence_path: Path,
    temporary_root: Path,
    capability_parent: Path,
    *,
    check: str,
    error: str,
) -> int:
    """Publish exact terminal absence before any Docker resource can exist."""

    job = BrowserSidecarJob(
        temporary_root,
        Path("/workspace/repo/outputs/conformance/formal"),
        job_id="formal-local-conformance",
        capability_parent=capability_parent,
    )
    job.first_error = check
    receipt = job.close(workload_status=None)
    return _publish_terminal_failure(evidence_path, receipt, error=error)


def _publish_terminal_failure(
    evidence_path: Path,
    receipt: Mapping[str, object],
    *,
    error: str,
) -> int:
    """Publish one already-closed failure receipt at a trusted target."""

    evidence = {
        "schema": "meshshot.browser-sidecar.local-conformance/1",
        "status": "failed",
        "error": error,
        "client": None,
        "receipt": receipt,
    }
    _write_atomic(evidence_path, evidence)
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 2


def _fixed_container_isolation(
    *,
    user: str | None = None,
    read_only_discovery: bool = False,
) -> list[str]:
    """Return fixed isolation with an explicit least-authority runtime user."""

    runtime_user = user if user is not None else f"{os.getuid()}:{os.getgid()}"
    home_uid, home_gid = runtime_user.split(":", 1)
    return [
        "--pull=never",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        *(
            ["--cap-add", "DAC_READ_SEARCH"]
            if read_only_discovery and runtime_user == "0:0"
            else []
        ),
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "32",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1" if read_only_discovery else "0.5",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m,mode=1777",
        "--tmpfs",
        (
            "/home/pwuser:rw,nosuid,nodev,size=16m,"
            f"uid={home_uid},gid={home_gid},mode=700"
        ),
        "--user",
        runtime_user,
    ]


def _run_docker(
    arguments: Sequence[str],
    *,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded conformance-owned Docker operation."""

    return subprocess.run(
        list(arguments),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _require_absent_container(docker: str, name: str, role: str) -> None:
    """Reject a foreign predictable name without adopting or deleting it."""

    absent = _run_docker([docker, "container", "inspect", name])
    if absent.returncode == 0:
        raise RuntimeError(f"foreign conformance-{role} name exists")
    if absent.returncode != 1:
        raise RuntimeError(f"conformance-{role} name absence is unproved")


def _verify_container_owner(
    docker: str,
    container_id: str,
    job: BrowserSidecarJob,
    role: str,
) -> None:
    """Bind a returned exact ID to both immutable owner labels."""

    projections = (
        (
            '{{index .Config.Labels "io.text-to-cad.browser-sidecar-job"}}',
            job.job_id,
            "job",
        ),
        (
            '{{index .Config.Labels "io.text-to-cad.browser-sidecar-owner"}}',
            job.owner_nonce,
            "owner",
        ),
    )
    for projection, expected, label in projections:
        inspected = _run_docker(
            [
                docker,
                "container",
                "inspect",
                container_id,
                "--format",
                projection,
            ]
        )
        if inspected.returncode != 0 or inspected.stdout.splitlines() != [expected]:
            raise RuntimeError(f"conformance-{role} {label} label mismatch")


def _create_owned_container(
    docker: str,
    job: BrowserSidecarJob,
    role: str,
    name: str,
    arguments: Sequence[str],
) -> str:
    """Create one container and return only its exact immutable ID."""

    _require_absent_container(docker, name, role)
    created = _run_docker(
        [
            docker,
            "create",
            "--name",
            name,
            "--label",
            job.label,
            "--label",
            job.owner_label,
            *arguments,
        ]
    )
    if created.returncode != 0:
        raise RuntimeError(f"fixed conformance-{role} create failed")
    container_id = created.stdout.strip()
    if browser_sidecar.RESOURCE_ID.fullmatch(container_id) is None:
        raise RuntimeError(f"fixed conformance-{role} identity is invalid")
    _verify_container_owner(docker, container_id, job, role)
    return container_id


def _remove_owned_container(docker: str, container_id: str, role: str) -> None:
    """Remove only the exact returned container ID."""

    removed = _run_docker([docker, "rm", "-f", container_id])
    if removed.returncode != 0:
        raise RuntimeError(f"fixed conformance-{role} cleanup failed")


def _discover_client_surface(
    docker: str,
    job: BrowserSidecarJob,
    container_name: str,
) -> Mapping[str, object]:
    """Run the image-sealed fixed discovery role without a host source mount."""

    from scripts.pilot.browser_gate_contract import (
        CONFORMANCE_OPTIONAL_ROOTS,
        CONFORMANCE_REQUIRED_ROOTS,
        CONFORMANCE_SURFACE_SCHEMA,
        CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS,
    )

    container_id: str | None = None
    primary: BaseException | None = None
    decoded: Any = None
    try:
        container_id = _create_owned_container(
            docker,
            job,
            "surface",
            container_name,
            [
                *_fixed_container_isolation(
                    user="0:0", read_only_discovery=True
                ),
                "--entrypoint",
                "python3",
                BROKER_IMAGE_ID,
                DISCOVERY_ARTIFACT_PATH,
                "--discover-conformance-surface",
            ],
        )
        completed = _run_docker(
            [docker, "start", "-a", container_id],
            timeout=CONFORMANCE_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise RuntimeError("fixed conformance-surface discovery failed")
        lines = completed.stdout.splitlines()
        if len(lines) != 1:
            raise RuntimeError("fixed conformance-surface result is malformed")
        decoded = _strict_json(lines[0].encode("ascii"), "conformance surface")
    except BaseException as exc:
        primary = exc
    cleanup: BaseException | None = None
    if container_id is not None:
        try:
            _remove_owned_container(docker, container_id, "surface")
        except BaseException as exc:
            cleanup = exc
    if cleanup is not None:
        raise cleanup
    if primary is not None:
        raise primary
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

    from scripts.pilot.runner import NestedGateChannel

    channel = NestedGateChannel(job.capability_dir)
    process: subprocess.Popen[str] | None = None
    container_id: str | None = None
    primary: BaseException | None = None
    outcome: tuple[int, Mapping[str, Any]] | None = None
    try:
        capability_dir = job.capability_dir.resolve()
        container_id = _create_owned_container(
            docker,
            job,
            "client",
            client_name,
            [
                *_fixed_container_isolation(),
                *_browser_mask_arguments(manifest),
                "--mount",
                (
                    "type=bind,src="
                    f"{capability_dir},dst=/run/meshshot-browser,readonly"
                ),
                "--entrypoint",
                "python3",
                BROKER_IMAGE_ID,
                browser_sidecar.NESTED_GATE["artifactPath"],
                "--",
                "/opt/text-to-cad/scripts/pilot/browser_sidecar_conformance.py",
                "client",
            ],
        )
        process = subprocess.Popen(
            [docker, "start", "-a", container_id],
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
        outcome = status, decoded
    except BaseException as exc:
        primary = exc
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    finally:
        try:
            channel.close()
        except BaseException as exc:
            if primary is None:
                primary = exc
    cleanup: BaseException | None = None
    if container_id is not None:
        try:
            _remove_owned_container(docker, container_id, "client")
        except BaseException as exc:
            cleanup = exc
    if cleanup is not None:
        raise cleanup
    if primary is not None:
        raise primary
    assert outcome is not None
    return outcome


def run_host(evidence_path: Path) -> int:
    """Own one exact job and one fixed networkless conformance client."""

    if not evidence_path.is_absolute():
        return 2
    try:
        capability_parent = evidence_path.parent.resolve(strict=True)
        parent_state = capability_parent.stat()
    except OSError:
        return 2
    canonical_evidence_path = capability_parent / evidence_path.name
    if evidence_path.parent != capability_parent:
        return 2
    if (
        not stat.S_ISDIR(parent_state.st_mode)
        or parent_state.st_uid != os.getuid()
        or stat.S_IMODE(parent_state.st_mode) != 0o700
    ):
        return 2
    with tempfile.TemporaryDirectory(
        prefix="meshshot-formal-conformance-", dir="/tmp"
    ) as temporary:
        from scripts.pilot.runner import _prepare_nested_browser_gate_from_manifest

        temporary_root = Path(temporary).resolve()
        docker = shutil.which("docker")
        if docker is None:
            return _publish_preflight_failure(
                canonical_evidence_path,
                temporary_root,
                capability_parent,
                check="docker-resolution",
                error="BrowserSidecarError: docker executable is unavailable",
            )
        try:
            job = BrowserSidecarJob.create(
                temporary_root,
                Path("/workspace/repo/outputs/conformance/formal"),
                job_id="formal-local-conformance",
                capability_parent=capability_parent,
            )
        except browser_sidecar.BrowserSidecarError as exc:
            if exc.terminal_receipt is not None:
                return _publish_terminal_failure(
                    canonical_evidence_path,
                    exc.terminal_receipt,
                    error=f"{type(exc).__name__}: {exc}",
                )
            return 2
        job.docker = docker
        client_name = f"{job.prefix}-client"
        surface_name = f"{job.prefix}-surface"
        client_result: Mapping[str, Any] | None = None
        client_status: int | None = None
        error: str | None = None
        try:
            manifest = _discover_client_surface(docker, job, surface_name)
            _prepare_nested_browser_gate_from_manifest(
                browser_sidecar.REPO_ROOT, job, manifest
            )
            job.start()
            client_status, client_result = _run_gate_then_client(
                docker, job, client_name, manifest
            )
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            client_status = 1 if client_status is None else client_status
        finally:
            receipt = job.close(workload_status=client_status)
        succeeded = client_status == 0 and receipt.get("status") == "succeeded"
        evidence = {
            "schema": "meshshot.browser-sidecar.local-conformance/1",
            "status": "succeeded" if succeeded else "failed",
            "error": error,
            "client": client_result,
            "receipt": receipt,
        }
        _write_atomic(canonical_evidence_path, evidence)
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
