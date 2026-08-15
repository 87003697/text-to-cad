#!/usr/bin/env python3
"""Run the sealed Browser Gate, then exec the already-selected workload."""

from __future__ import annotations

from io import BytesIO
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
from typing import Any, Mapping, Sequence

try:
    from scripts.pilot.browser_gate_contract import (
        CONFORMANCE_OPTIONAL_ROOTS,
        CONFORMANCE_REQUIRED_ROOTS,
        CONFORMANCE_SURFACE_SCHEMA,
        CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS,
    )
    from scripts.pilot.browser_surface import (
        canonicalize_browser_masks,
        discover_browser_roots,
    )
except ModuleNotFoundError:
    from browser_gate_contract import (  # type: ignore[no-redef]
        CONFORMANCE_OPTIONAL_ROOTS,
        CONFORMANCE_REQUIRED_ROOTS,
        CONFORMANCE_SURFACE_SCHEMA,
        CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS,
    )
    from browser_surface import (  # type: ignore[no-redef]
        canonicalize_browser_masks,
        discover_browser_roots,
    )

from PIL import Image
from meshshot import MeshGeometry, render_residual_preview


CONTRACT = json.loads(
    files("meshshot").joinpath("browser_contract.json").read_text(encoding="utf-8")
)
GATE = CONTRACT["nestedGate"]
AUTHORITY_PATH = Path(CONTRACT["authorityPath"])
BROKER_SOCKET_PATH = Path(CONTRACT["socketPath"])
GATE_SOCKET_PATH = Path(GATE["socketPath"])
GATE_INPUT_PATH = Path(GATE["inputPath"])
GATE_ARTIFACT_PATH = Path(GATE["artifactPath"])
MAX_PROOF_BYTES = GATE["maxProofBytes"]
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


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


def _fixed_bytes(path: Path, *, mode: int, limit: int) -> bytes:
    """Read one exact outer-owned regular inode without following links."""

    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise ValueError("fixed gate input identity mismatch")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if not raw or len(raw) > limit:
        raise ValueError("fixed gate input size mismatch")
    return raw


def _artifact_sha256() -> str:
    """Hash the exact read-only zipapp selected by the outer runner."""

    return hashlib.sha256(GATE_ARTIFACT_PATH.read_bytes()).hexdigest()


def load_gate_identity() -> Mapping[str, Any]:
    """Validate the sealed artifact and exact job-bound read-only input."""

    value = _strict_json(_fixed_bytes(GATE_INPUT_PATH, mode=0o444, limit=16384), "gate input")
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "jobId",
        "nonce",
        "artifactSha256",
        "surfaceManifest",
    }:
        raise ValueError("gate input schema mismatch")
    manifest = value["surfaceManifest"]
    if (
        value["schema"] != GATE["inputSchema"]
        or not isinstance(value["jobId"], str)
        or not isinstance(value["nonce"], str)
        or not isinstance(value["artifactSha256"], str)
        or HEX_64.fullmatch(value["artifactSha256"]) is None
        or value["artifactSha256"] != _artifact_sha256()
        or not isinstance(manifest, dict)
        or set(manifest) != {"schema", "scanRoots", "browserExclusions"}
        or manifest["schema"] != GATE["surfaceSchema"]
        or not isinstance(manifest["scanRoots"], list)
        or not all(isinstance(root, str) and root.startswith("/") for root in manifest["scanRoots"])
        or not isinstance(manifest["browserExclusions"], list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"kind", "target", "mask"}
            or item.get("kind") not in {"package", "executable", "cache"}
            or not isinstance(item.get("target"), str)
            or not item["target"].startswith("/")
            or item.get("mask") not in {"tmpfs", "dev-null"}
            for item in manifest["browserExclusions"]
        )
    ):
        raise ValueError("gate input identity mismatch")
    if manifest["browserExclusions"] != canonicalize_browser_masks(
        manifest["browserExclusions"]
    ):
        raise ValueError("gate input identity mismatch")
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {
        **value,
        "surfaceManifestSha256": hashlib.sha256(canonical).hexdigest(),
    }


def _authority(identity: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read and cross-bind the fixed formal authority to the gate input."""

    payload = _strict_json(_fixed_bytes(AUTHORITY_PATH, mode=0o444, limit=16384), "authority")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "jobId", "gateNonce", "imageId", "programs"}
        or payload.get("schema") != CONTRACT["authoritySchema"]
        or payload.get("jobId") != identity["jobId"]
        or payload.get("gateNonce") != identity["nonce"]
        or payload.get("imageId") != CONTRACT["sidecarImageId"]
        or payload.get("programs") != CONTRACT["programs"]
    ):
        raise ValueError("fixed authority identity mismatch")
    return payload


def _viewer_request(identity: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Execute the one registered Viewer projection operation."""

    identity = load_gate_identity() if identity is None else identity
    authority = _authority(identity)
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
        while chunk := connection.recv(65536):
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
        or set(result) != {
            "title", "modelKey", "programDigest", "screenshotDataUrl",
            "screenshotSha256", "screenshotBytes", "bodyMentionsFixture",
            "bodyHasArtifactError", "inspection",
        }
        or result.get("modelKey") != "inspection-step"
        or result.get("programDigest") != CONTRACT["programs"]["viewer"]
        or result.get("bodyMentionsFixture") is not True
        or result.get("bodyHasArtifactError") is not False
        or not isinstance(inspection, dict)
        or inspection.get("before") != "Display and projection: Solid, Orthographic"
        or inspection.get("after") != "Display and projection: Solid, Perspective"
        or inspection.get("changed") is not True
    ):
        raise ValueError("Viewer projection predicate failed")
    return result


def _browser_processes() -> list[str]:
    """Inventory Chromium processes in the gate/Agent PID namespace."""

    found: list[str] = []
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            command = (process_dir / "cmdline").read_bytes().replace(b"\0", b" ")
            name = (process_dir / "comm").read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            if not process_dir.exists():
                continue
            raise ValueError("cannot prove browser process inventory") from exc
        except OSError as exc:
            raise ValueError("cannot prove browser process inventory") from exc
        process_text = f"{name} {command.decode(errors='replace')}"
        if re.search(
            r"(?:^|[/\s])(?:chromium(?:-browser)?|google-chrome(?:-(?:stable|beta|unstable))?|chrome(?:-headless-shell|_crashpad_handler)?|headless[_-]shell)(?:\s|$)",
            process_text,
            re.I,
        ):
            found.append(name)
    return found


def _exclusions_closed(exclusions: Sequence[Mapping[str, Any]]) -> bool:
    """Verify every outer-discovered browser root is replaced by an empty mask."""

    try:
        if list(exclusions) != canonicalize_browser_masks(exclusions):
            return False
        null_device = Path("/dev/null").stat().st_rdev
        for item in exclusions:
            target = Path(item["target"])
            metadata = target.stat()
            if item["mask"] == "tmpfs":
                if not target.is_dir() or any(target.iterdir()):
                    return False
            elif not stat.S_ISCHR(metadata.st_mode) or metadata.st_rdev != null_device:
                return False
    except OSError:
        return False
    return True


def run_gate_checks(identity: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Run public parity and prove the fixed Agent browser surface is empty."""

    identity = load_gate_identity() if identity is None else identity
    _authority(identity)
    reference = MeshGeometry(
        vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    candidate = MeshGeometry(
        vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    rendered = render_residual_preview(reference, candidate, variant="step", exterior_directions=[])
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        image.load()
        residual = {
            "pngSha256": hashlib.sha256(rendered.png_bytes).hexdigest(),
            "mode": image.mode,
            "size": list(image.size),
            "profileSha256": rendered.profile_sha256,
            "views": [view["name"] for view in rendered.views],
        }
    expected_residual = {
        "pngSha256": GATE["publicPngSha256"],
        "mode": "RGB",
        "size": [504, 1008],
        "profileSha256": GATE["profileSha256"],
        "views": GATE["views"],
    }
    if residual != expected_residual:
        raise ValueError("public residual parity predicate failed")
    viewer_result = _viewer_request(identity)
    viewer = {
        "before": viewer_result["inspection"]["before"],
        "after": viewer_result["inspection"]["after"],
        "bodyMentionsFixture": viewer_result["bodyMentionsFixture"],
        "bodyHasArtifactError": viewer_result["bodyHasArtifactError"],
    }
    manifest = identity["surfaceManifest"]
    if not _exclusions_closed(manifest["browserExclusions"]):
        raise ValueError("nested browser exclusion predicate failed")
    visible = discover_browser_roots(
        ((Path(root), Path(root), True) for root in manifest["scanRoots"]),
        permitted_symlink_roots=[
            *(Path(root) for root in manifest["scanRoots"]),
            Path("/dev/null"),
            Path("/proc/mounts"),
        ],
        permitted_dangling_symlink_roots=[
            *(Path(root) for root in manifest["scanRoots"]),
        ],
    )
    excluded_targets = {item["target"] for item in manifest["browserExclusions"]}
    visible = [item for item in visible if item["target"] not in excluded_targets]
    processes = _browser_processes()
    inventory = {
        "browserExecutables": [item["target"] for item in visible if item["kind"] == "executable"],
        "browserPackages": [item["target"] for item in visible if item["kind"] == "package"],
        "browserCaches": [item["target"] for item in visible if item["kind"] == "cache"],
        "browserProcesses": processes,
    }
    predicates = {
        "publicResidualParity": True,
        "viewerProjectionChanged": True,
        "viewerArtifactClean": True,
        "browserInventoryEmpty": not visible,
        "browserProcessZero": not processes,
    }
    if any(value is not True for value in predicates.values()):
        raise ValueError("nested browser predicate failed")
    return {
        "schema": GATE["schema"],
        "status": "succeeded",
        "jobId": identity["jobId"],
        "nonce": identity["nonce"],
        "artifactSha256": identity["artifactSha256"],
        "surfaceManifestSha256": identity["surfaceManifestSha256"],
        "predicates": predicates,
        "residual": residual,
        "viewer": viewer,
        "inventory": inventory,
    }


def discover_conformance_surface() -> Mapping[str, Any]:
    """Inventory the fixed immutable client-image surface before Sidecar start."""

    roots = [*CONFORMANCE_REQUIRED_ROOTS]
    roots.extend(
        root
        for root in CONFORMANCE_OPTIONAL_ROOTS
        if Path(root).exists() or Path(root).is_symlink()
    )
    direct_exclusions: list[dict[str, str]] = []
    for root in CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS:
        path = Path(root)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("conformance browser root is a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            roots.append(root)
            direct_exclusions.append(
                {"kind": "cache", "target": root, "mask": "tmpfs"}
            )
        elif stat.S_ISREG(metadata.st_mode):
            roots.append(root)
            direct_exclusions.append(
                {"kind": "executable", "target": root, "mask": "dev-null"}
            )
        else:
            raise ValueError("conformance browser root type is invalid")
    roots = sorted(set(roots))
    findings = discover_browser_roots(
        ((Path(root), Path(root), True) for root in roots),
        permitted_symlink_roots=[
            *(Path(root) for root in roots),
            Path("/dev/null"),
            Path("/proc/mounts"),
        ],
        permitted_dangling_symlink_roots=[*(Path(root) for root in roots)],
    )
    exclusions = canonicalize_browser_masks([*findings, *direct_exclusions])
    return {
        "schema": CONFORMANCE_SURFACE_SCHEMA,
        "scanRoots": roots,
        "browserExclusions": exclusions,
    }


def _failed_proof(identity: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return one identity-bound closed proof without exception text."""

    return {
        "schema": GATE["schema"], "status": "failed",
        "jobId": identity["jobId"], "nonce": identity["nonce"],
        "artifactSha256": identity["artifactSha256"],
        "surfaceManifestSha256": identity["surfaceManifestSha256"],
        "predicates": {name: False for name in GATE["predicates"]},
        "residual": {"pngSha256": "0" * 64, "mode": "", "size": [0, 0], "profileSha256": "0" * 64, "views": []},
        "viewer": {"before": "", "after": "", "bodyMentionsFixture": False, "bodyHasArtifactError": True},
        "inventory": {"browserExecutables": [], "browserPackages": [], "browserCaches": [], "browserProcesses": []},
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
            return connection.recv(2) == b"\x01" and connection.recv(1) == b""
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate with no render inputs, then replace this exact PID."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--discover-conformance-surface"]:
        try:
            result = discover_conformance_surface()
        except Exception:
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if not arguments or arguments[0] != "--" or len(arguments) == 1:
        return 2
    try:
        identity = load_gate_identity()
    except Exception:
        return 1
    try:
        proof = run_gate_checks(identity)
    except Exception:
        publish_and_wait(_failed_proof(identity))
        return 1
    if not publish_and_wait(proof):
        return 1
    workload = arguments[1:]
    os.execvpe(workload[0], workload, os.environ.copy())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
