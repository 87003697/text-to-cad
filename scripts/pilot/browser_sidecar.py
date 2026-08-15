#!/usr/bin/env python3
"""Own one exact Browser Sidecar and its registered-program broker per pilot."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPO_ROOT
    / "packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json"
)
CONTRACT_PATH = REPO_ROOT / "packages/meshshot/src/meshshot/browser_contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


IMAGE_ID = CONTRACT["sidecarImageId"]
IMAGE_SOURCE_REVISION = "1abe4c97929906b5c0b28b0f3f38857bd923952f"
BROKER_BASE_IMAGE_ID = (
    "sha256:a2dae48401a6918a15e68a97c4c0290ba6a58ec47a3448498aec12885be46373"
)
BROKER_LOCK_PATH = (
    REPO_ROOT / "packages/meshshot/browser_sidecar_broker/image-lock.json"
)


def _broker_lock() -> tuple[str, str]:
    """Load the reviewed Broker identity, or a permanently closed sentinel."""

    try:
        payload = json.loads(BROKER_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "sha256:" + "0" * 64, "unbuilt"
    if (
        not isinstance(payload, dict)
        or set(payload) != {"imageId", "sourceRevision", "baseImageId"}
        or not isinstance(payload.get("imageId"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", payload["imageId"]) is None
        or payload.get("imageId") == "sha256:" + "0" * 64
        or not isinstance(payload.get("sourceRevision"), str)
        or re.fullmatch(r"[0-9a-f]{40}", payload["sourceRevision"]) is None
        or payload.get("baseImageId") != BROKER_BASE_IMAGE_ID
    ):
        return "sha256:" + "0" * 64, "unbuilt"
    return payload["imageId"], payload["sourceRevision"]


BROKER_IMAGE_ID, BROKER_IMAGE_SOURCE_REVISION = _broker_lock()
PROGRAMS = CONTRACT["programs"]
AUTHORITY_SCHEMA = CONTRACT["authoritySchema"]
BROKER_SCHEMA = "meshshot.browser-sidecar.broker/1"
RECEIPT_SCHEMA = "meshshot.browser-sidecar.job-receipt/2"
REQUEST_SCHEMA = CONTRACT["requestSchema"]
RESPONSE_SCHEMA = CONTRACT["responseSchema"]
SANDBOX_AUTHORITY_PATH = Path(CONTRACT["authorityPath"])
SANDBOX_SOCKET_PATH = Path(CONTRACT["socketPath"])
NESTED_GATE = CONTRACT["nestedGate"]
NESTED_GATE_SCHEMA = NESTED_GATE["schema"]
NESTED_GATE_SOCKET_PATH = Path(NESTED_GATE["socketPath"])
RECEIPT_PREDICATES = (
    "sidecarReady",
    "brokerReady",
    "sidecarSourceHidden",
    "sidecarEgressBlocked",
    "socketFixed",
    "brokerResidualAccepted",
    "brokerResidualEightView",
    "brokerViewerAccepted",
    "brokerViewerProjectionChanged",
    "brokerViewerArtifactClean",
    "brokerFreshContextsExact",
    "nestedPublicResidualParity",
    "nestedViewerProjectionChanged",
    "nestedViewerArtifactClean",
    "nestedBrowserInventoryEmpty",
    "nestedBrowserProcessZero",
    "brokerTerminalZero",
    "sidecarClosingExact",
    "sidecarTerminalZero",
    "workloadTerminalZero",
    "absenceProved",
)
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
RESOURCE_ID = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_PROJECTIONS = (
    ("id", "{{.Id}}", IMAGE_ID),
    ("os", "{{.Os}}", "linux"),
    ("architecture", "{{.Architecture}}", "amd64"),
    (
        "revision",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        IMAGE_SOURCE_REVISION,
    ),
)
BROKER_IMAGE_PROJECTIONS = (
    ("id", "{{.Id}}", BROKER_IMAGE_ID),
    ("os", "{{.Os}}", "linux"),
    ("architecture", "{{.Architecture}}", "amd64"),
    (
        "revision",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        BROKER_IMAGE_SOURCE_REVISION,
    ),
    (
        "base",
        '{{index .Config.Labels "io.text-to-cad.browser-sidecar-broker-base"}}',
        BROKER_BASE_IMAGE_ID,
    ),
)
VIEW_ORDER = ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
OUTSIDE_DIRECTIONS = frozenset({"-x", "+x", "-y", "+y", "-z", "+z"})
MAX_REQUEST_BYTES = 1024 * 1024


class BrowserSidecarError(RuntimeError):
    """One closed formal-pilot Browser Sidecar lifecycle failure."""

    def __init__(self, message: str, *, check: str) -> None:
        super().__init__(message)
        self.check = check


def _strict_json(raw: str, label: str) -> Any:
    """Decode duplicate-free JSON from one fixed Docker/broker projection."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BrowserSidecarError(
                    f"{label} contains duplicate keys",
                    check=f"{label}-format",
                )
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise BrowserSidecarError(
            f"{label} is not JSON",
            check=f"{label}-format",
        ) from exc


def _write_json_atomic(path: Path, payload: object, *, mode: int = 0o644) -> None:
    """Atomically publish one canonical JSON receipt or authority file."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(mode)
    os.replace(temporary, path)


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    """Require one object with exactly the registered public keys."""

    if not isinstance(value, dict) or set(value) != keys:
        raise BrowserSidecarError(
            f"{label} schema is invalid",
            check=f"{label}-schema",
        )
    return value


def validate_nested_gate_proof(
    value: Any,
    *,
    expected_job_id: str,
    expected_nonce: str,
    expected_artifact_sha256: str,
    expected_surface_manifest_sha256: str,
) -> Mapping[str, bool]:
    """Validate the exact fixed proof produced before Agent exec."""

    proof = _exact_object(
        value,
        {
            "schema",
            "status",
            "jobId",
            "nonce",
            "artifactSha256",
            "surfaceManifestSha256",
            "predicates",
            "residual",
            "viewer",
            "inventory",
        },
        "nested-gate-proof",
    )
    predicates = _exact_object(
        proof["predicates"], set(NESTED_GATE["predicates"]), "nested-gate-predicates"
    )
    residual = _exact_object(
        proof["residual"],
        {"pngSha256", "mode", "size", "profileSha256", "views"},
        "nested-gate-residual",
    )
    viewer = _exact_object(
        proof["viewer"],
        {"before", "after", "bodyMentionsFixture", "bodyHasArtifactError"},
        "nested-gate-viewer",
    )
    inventory = _exact_object(
        proof["inventory"],
        {
            "browserExecutables",
            "browserPackages",
            "browserCaches",
            "browserProcesses",
        },
        "nested-gate-inventory",
    )
    if (
        proof["schema"] != NESTED_GATE_SCHEMA
        or proof["status"] != "succeeded"
        or proof["jobId"] != expected_job_id
        or proof["nonce"] != expected_nonce
        or proof["artifactSha256"] != expected_artifact_sha256
        or proof["surfaceManifestSha256"] != expected_surface_manifest_sha256
        or any(value is not True for value in predicates.values())
        or residual
        != {
            "pngSha256": NESTED_GATE["publicPngSha256"],
            "mode": "RGB",
            "size": [504, 1008],
            "profileSha256": NESTED_GATE["profileSha256"],
            "views": NESTED_GATE["views"],
        }
        or viewer
        != {
            "before": "Display and projection: Solid, Orthographic",
            "after": "Display and projection: Solid, Perspective",
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
        }
        or inventory
        != {
            "browserExecutables": [],
            "browserPackages": [],
            "browserCaches": [],
            "browserProcesses": [],
        }
    ):
        raise BrowserSidecarError(
            "nested Browser Gate proof failed",
            check="nested-gate-proof",
        )
    return dict(predicates)


def _geometry(value: Any, label: str) -> dict[str, Any]:
    """Validate one bounded indexed-triangle geometry payload."""

    geometry = _exact_object(value, {"vertices", "faces"}, label)
    vertices = geometry["vertices"]
    faces = geometry["faces"]
    if (
        not isinstance(vertices, list)
        or not 0 < len(vertices) <= 10_000
        or any(
            not isinstance(vertex, list)
            or len(vertex) != 3
            or any(
                not isinstance(coordinate, (int, float))
                or isinstance(coordinate, bool)
                or not math.isfinite(coordinate)
                for coordinate in vertex
            )
            for vertex in vertices
        )
    ):
        raise BrowserSidecarError(
            f"{label} vertices are invalid",
            check=f"{label}-geometry",
        )
    if (
        not isinstance(faces, list)
        or not 0 < len(faces) <= 20_000
        or any(
            not isinstance(face, list)
            or len(face) != 3
            or any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(vertices)
                for index in face
            )
            for face in faces
        )
    ):
        raise BrowserSidecarError(
            f"{label} faces are invalid",
            check=f"{label}-geometry",
        )
    return geometry


class RegisteredProgramBroker:
    """Public exact-schema adapter over one outer-owned Playwright connection."""

    def __init__(self, browser: Any, job_id: str) -> None:
        """Bind one exact job and immutable profile to a stable connection."""

        if JOB_ID.fullmatch(job_id) is None:
            raise BrowserSidecarError("job identity is invalid", check="job-id")
        self.browser = browser
        self.job_id = job_id
        try:
            profile = _strict_json(
                PROFILE_PATH.read_text(encoding="utf-8"),
                "residual-profile",
            )
        except OSError as exc:
            raise BrowserSidecarError(
                "residual profile is unavailable",
                check="residual-profile",
            ) from exc
        if not isinstance(profile, dict):
            raise BrowserSidecarError(
                "residual profile is invalid",
                check="residual-profile",
            )
        self.profile = profile
        self.request_count = 0
        self.program_counts = {"residual": 0, "viewer": 0}
        self.program_predicates = {
            "residualEightView": False,
            "viewerProjectionChanged": False,
            "viewerArtifactClean": False,
        }

    def preflight(self) -> Mapping[str, object]:
        """Prove exact baked authority, Source-Hidden, and blocked egress."""

        context = self.browser.new_context(
            viewport={"width": 64, "height": 64},
            device_scale_factor=1,
        )
        try:
            page = context.new_page()
            page.goto(
                "http://127.0.0.1:4174/render.html",
                wait_until="load",
                timeout=60_000,
            )
            result = page.evaluate(
                """async () => {
                  const response = await fetch("http://127.0.0.1:3001/v1/authority");
                  if (!response.ok) throw new Error("authority endpoint failed");
                  const authority = await response.json();
                  let externalEgressBlocked = false;
                  try {
                    await fetch("https://example.com/", { signal: AbortSignal.timeout(5000) });
                  } catch {
                    externalEgressBlocked = true;
                  }
                  return { authority, externalEgressBlocked };
                }"""
            )
        finally:
            context.close()
        result = _exact_object(
            result,
            {"authority", "externalEgressBlocked"},
            "isolation-preflight",
        )
        authority = result["authority"]
        if (
            not isinstance(authority, dict)
            or set(authority)
            != {
                "schema",
                "jobId",
                "endpointPath",
                "browserPid",
                "chromiumRevision",
                "chromiumVersion",
                "playwrightVersion",
                "programs",
                "sourceAliasesVisible",
            }
            or authority.get("schema") != "meshshot.browser-sidecar.prototype/1"
            or authority.get("jobId") != self.job_id
            or authority.get("programs") != PROGRAMS
            or authority.get("sourceAliasesVisible") != []
            or authority.get("chromiumRevision") != "1223"
            or authority.get("chromiumVersion") != "148.0.7778.96"
            or authority.get("playwrightVersion") != "1.60.0"
            or not isinstance(authority.get("browserPid"), int)
            or isinstance(authority.get("browserPid"), bool)
            or authority["browserPid"] <= 0
            or result["externalEgressBlocked"] is not True
        ):
            raise BrowserSidecarError(
                "Sidecar isolation predicates failed",
                check="isolation-preflight",
            )
        return {
            "sourceAliasesVisible": [],
            "externalEgressBlocked": True,
            "browserPid": authority["browserPid"],
        }

    def _residual_payload(self, value: Any) -> dict[str, Any]:
        """Validate the only formal eight-view residual input schema."""

        payload = _exact_object(
            value,
            {"reference", "candidate", "variant", "exteriorDirections", "options"},
            "residual-payload",
        )
        reference = _geometry(payload["reference"], "reference")
        candidate = _geometry(payload["candidate"], "candidate")
        if payload["variant"] not in {"step", "final"}:
            raise BrowserSidecarError(
                "residual variant is invalid",
                check="residual-variant",
            )
        directions = payload["exteriorDirections"]
        if (
            not isinstance(directions, list)
            or len(set(directions)) != len(directions)
            or any(direction not in OUTSIDE_DIRECTIONS for direction in directions)
        ):
            raise BrowserSidecarError(
                "residual directions are invalid",
                check="residual-directions",
            )
        options = _exact_object(
            payload["options"],
            {"cameraPolicy", "canonicalPostprocess"},
            "residual-options",
        )
        if options != {
            "cameraPolicy": "profile-fixed",
            "canonicalPostprocess": True,
        }:
            raise BrowserSidecarError(
                "residual options are not registered",
                check="residual-options",
            )
        return {
            "profile": self.profile,
            "variant": payload["variant"],
            "reference": reference,
            "candidate": candidate,
            "exteriorDirections": directions,
        }

    def execute(self, value: Any) -> Mapping[str, object]:
        """Execute one exact Render Program in a fresh context and page."""

        request = _exact_object(
            value,
            {"schema", "jobId", "imageId", "program", "payload"},
            "render-request",
        )
        if (
            request["schema"] != REQUEST_SCHEMA
            or request["jobId"] != self.job_id
            or request["imageId"] != IMAGE_ID
            or request["program"] not in PROGRAMS
        ):
            raise BrowserSidecarError(
                "render request identity is invalid",
                check="render-request-identity",
            )
        program = request["program"]
        if program == "residual":
            payload = self._residual_payload(request["payload"])
        else:
            viewer_payload = _exact_object(
                request["payload"],
                {"modelKey", "inspectionControl"},
                "viewer-payload",
            )
            if viewer_payload != {
                "modelKey": "inspection-step",
                "inspectionControl": "toggle-projection",
            }:
                raise BrowserSidecarError(
                    "Viewer request is not registered",
                    check="viewer-payload",
                )
            payload = viewer_payload
        context = self.browser.new_context(
            viewport=(
                {"width": 64, "height": 64}
                if program == "residual"
                else {"width": 1024, "height": 768}
            ),
            device_scale_factor=1,
        )
        try:
            page = context.new_page()
            if program == "viewer":
                page.goto(
                    "http://127.0.0.1:4173/?file=browser_sidecar_inspection.step",
                    wait_until="networkidle",
                    timeout=60_000,
                )
                page.wait_for_timeout(2_000)
                before = page.evaluate(
                    """async () => {
                      const selector = 'button[aria-label^="Display and projection:"]';
                      const deadline = Date.now() + 25000;
                      let control = null;
                      while (Date.now() < deadline) {
                        control = document.querySelector(selector);
                        if (control) break;
                        await new Promise((resolve) => setTimeout(resolve, 50));
                      }
                      const label = control?.getAttribute("aria-label") || null;
                      if (!(control instanceof HTMLButtonElement) ||
                          label !== "Display and projection: Solid, Orthographic") {
                        throw new Error(`unexpected projection control: ${label}`);
                      }
                      control.focus();
                      if (document.activeElement !== control) {
                        throw new Error("projection control did not accept focus");
                      }
                      return label;
                    }"""
                )
                target = "Perspective"
                page.keyboard.press("Enter")
                page.evaluate(
                    """async (target) => {
                      const deadline = Date.now() + 5000;
                      let item = null;
                      while (Date.now() < deadline) {
                        item = [...document.querySelectorAll('[role="menuitem"]')]
                          .find((element) => element.textContent?.trim().startsWith(target));
                        if (item) break;
                        await new Promise((resolve) => setTimeout(resolve, 50));
                      }
                      if (!(item instanceof HTMLElement)) throw new Error("menu item missing");
                      item.focus();
                      if (document.activeElement !== item) throw new Error("menu focus failed");
                    }""",
                    target,
                )
                page.keyboard.press("Enter")
                after = page.evaluate(
                    """async (target) => {
                      const selector = 'button[aria-label^="Display and projection:"]';
                      const expected = `Display and projection: Solid, ${target}`;
                      const deadline = Date.now() + 5000;
                      while (Date.now() < deadline) {
                        const label = document.querySelector(selector)?.getAttribute("aria-label") || null;
                        if (label === expected) return label;
                        await new Promise((resolve) => setTimeout(resolve, 50));
                      }
                      throw new Error(`projection control did not reach ${expected}`);
                    }""",
                    target,
                )
                screenshot = page.screenshot(type="png", timeout=120_000)
                body = page.locator("body").inner_text()
                title = page.title()
                if (
                    title != "CAD Viewer | browser_sidecar_inspection.step"
                    or "browser_sidecar_inspection.step" not in body
                    or "STEP artifact missing" in body
                    or "Generated GLB is missing" in body
                    or before != "Display and projection: Solid, Orthographic"
                    or after != "Display and projection: Solid, Perspective"
                ):
                    raise BrowserSidecarError(
                        "Viewer predicates failed",
                        check="viewer-result",
                    )
                result = {
                    "title": title,
                    "modelKey": payload["modelKey"],
                    "programDigest": PROGRAMS["viewer"],
                    "screenshotDataUrl": "data:image/png;base64,"
                    + base64.b64encode(screenshot).decode("ascii"),
                    "screenshotSha256": hashlib.sha256(screenshot).hexdigest(),
                    "screenshotBytes": len(screenshot),
                    "bodyMentionsFixture": True,
                    "bodyHasArtifactError": False,
                    "inspection": {
                        "control": payload["inspectionControl"],
                        "before": before,
                        "target": target,
                        "after": after,
                        "changed": before != after and target in after,
                    },
                }
                self.request_count += 1
                self.program_counts["viewer"] += 1
                self.program_predicates["viewerProjectionChanged"] = True
                self.program_predicates["viewerArtifactClean"] = True
                return {
                    "schema": RESPONSE_SCHEMA,
                    "jobId": self.job_id,
                    "imageId": IMAGE_ID,
                    "program": "viewer",
                    "result": result,
                }
            page.goto(
                "http://127.0.0.1:4174/render.html",
                wait_until="load",
                timeout=120_000,
            )
            page.wait_for_function(
                "typeof window.__meshshotRender === 'function'",
                timeout=120_000,
            )
            result = page.evaluate(
                "(renderPayload) => window.__meshshotRender(renderPayload)",
                payload,
            )
            result = _exact_object(
                result,
                {"ok", "pngDataUrl", "views"},
                "residual-result",
            )
            views = result["views"]
            if (
                result["ok"] is not True
                or not isinstance(result["pngDataUrl"], str)
                or not result["pngDataUrl"].startswith("data:image/png;base64,")
                or not isinstance(views, list)
                or tuple(
                    view.get("name") if isinstance(view, dict) else None
                    for view in views
                )
                != VIEW_ORDER
            ):
                raise BrowserSidecarError(
                    "residual result predicates failed",
                    check="residual-result",
                )
            self.request_count += 1
            self.program_counts["residual"] += 1
            self.program_predicates["residualEightView"] = True
            return {
                "schema": RESPONSE_SCHEMA,
                "jobId": self.job_id,
                "imageId": IMAGE_ID,
                "program": "residual",
                "result": result,
            }
        finally:
            context.close()


class BrowserSidecarJob:
    """Public adapter owning one exact OCI Sidecar for one pilot job."""

    def __init__(
        self,
        exp_dir: Path,
        sandbox_exp_dir: Path,
        *,
        job_id: str,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        """Bind immutable identities before any Docker resource is created."""

        if JOB_ID.fullmatch(job_id) is None:
            raise BrowserSidecarError("job identity is invalid", check="job-id")
        self.exp_dir = exp_dir.resolve()
        self.sandbox_exp_dir = sandbox_exp_dir
        self.job_id = job_id
        self.cancelled = cancelled or (lambda: False)
        self.run_dir = self.exp_dir / "run"
        self.receipt_path = self.run_dir / "browser-sidecar-receipt.json"
        self.owner_nonce = secrets.token_hex(16)
        self.gate_nonce = self.owner_nonce
        self.capability_dir = Path(
            tempfile.mkdtemp(prefix=f"meshshot-browser-{self.owner_nonce[:8]}-")
        ).resolve()
        self.authority_path = self.capability_dir / "authority.json"
        self.socket_path = self.capability_dir / SANDBOX_SOCKET_PATH.name
        self.nested_gate_socket_path = (
            self.capability_dir / NESTED_GATE_SOCKET_PATH.name
        )
        self.gate_artifact_path = self.capability_dir / Path(NESTED_GATE["artifactPath"]).name
        self.gate_input_path = self.capability_dir / Path(NESTED_GATE["inputPath"]).name
        self.prefix = f"ttc-bs-{self.owner_nonce[:12]}"
        self.network_name = f"{self.prefix}-net"
        self.container_name = f"{self.prefix}-sidecar"
        self.broker_container_name = f"{self.prefix}-broker"
        self.label = f"io.text-to-cad.browser-sidecar-job={self.job_id}"
        self.owner_label = (
            f"io.text-to-cad.browser-sidecar-owner={self.owner_nonce}"
        )
        self.docker: str | None = None
        self.network_id: str | None = None
        self.container_id: str | None = None
        self.broker_container_id: str | None = None
        self.socket_identity: tuple[int, int] | None = None
        self.readiness: Mapping[str, Any] | None = None
        self.broker_readiness: Mapping[str, Any] | None = None
        self.broker_terminal: Mapping[str, Any] | None = None
        self.nested_gate_predicates: Mapping[str, bool] | None = None
        self.gate_artifact_sha256: str | None = None
        self.surface_manifest_sha256: str | None = None
        self.gate_file_identities: dict[Path, tuple[int, int]] = {}
        self.request_count = 0
        self.first_error: str | None = None
        self.cleanup_errors: list[str] = []
        self._closed = False

    @property
    def sandbox_authority_path(self) -> Path:
        """Return the fixed authority path visible inside the pilot sandbox."""

        return SANDBOX_AUTHORITY_PATH

    def _check_cancelled(self) -> None:
        """Close startup at every boundary after an outer INT/TERM."""

        if self.cancelled():
            raise BrowserSidecarError(
                "Browser Sidecar startup was interrupted",
                check="startup-signal",
            )

    def record_nested_gate(self, proof: Any) -> None:
        """Record the outer-validated one-shot proof before Agent exec."""

        if self.nested_gate_predicates is not None:
            raise BrowserSidecarError(
                "nested Browser Gate proof was already recorded",
                check="nested-gate-duplicate",
            )
        if self.gate_artifact_sha256 is None or self.surface_manifest_sha256 is None:
            raise BrowserSidecarError(
                "nested Browser Gate identity is unavailable",
                check="nested-gate-proof",
            )
        self.nested_gate_predicates = validate_nested_gate_proof(
            proof,
            expected_job_id=self.job_id,
            expected_nonce=self.gate_nonce,
            expected_artifact_sha256=self.gate_artifact_sha256,
            expected_surface_manifest_sha256=self.surface_manifest_sha256,
        )

    def configure_nested_gate(
        self,
        *,
        artifact_sha256: str,
        surface_manifest_sha256: str,
    ) -> None:
        """Bind one sealed gate artifact and surface manifest before startup."""

        if (
            self.gate_artifact_sha256 is not None
            or re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", surface_manifest_sha256) is None
        ):
            raise BrowserSidecarError(
                "nested Browser Gate identity is invalid",
                check="nested-gate-identity",
            )
        self.gate_artifact_sha256 = artifact_sha256
        self.surface_manifest_sha256 = surface_manifest_sha256
        for path in (self.gate_artifact_path, self.gate_input_path):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o444
            ):
                raise BrowserSidecarError(
                    "nested Browser Gate file identity is invalid",
                    check="nested-gate-identity",
                )
            self.gate_file_identities[path] = (metadata.st_dev, metadata.st_ino)

    def _docker(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        """Run one fixed Docker command with bounded output and timeout."""

        if self.docker is None:
            raise BrowserSidecarError(
                "Docker was not resolved",
                check="docker-access",
            )
        try:
            completed = subprocess.run(
                [self.docker, *arguments],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrowserSidecarError(
                "fixed Docker operation failed",
                check=f"docker-{arguments[0]}-access",
            ) from exc
        if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
            raise BrowserSidecarError(
                "fixed Docker output exceeded its bound",
                check=f"docker-{arguments[0]}-format",
            )
        if check and completed.returncode:
            raise BrowserSidecarError(
                "fixed Docker operation returned nonzero",
                check=f"docker-{arguments[0]}-status",
            )
        return completed

    def _require_absent_name(self, kind: str, name: str) -> None:
        """Reject a foreign predictable name without adopting or deleting it."""

        existing = self._docker(kind, "inspect", name, check=False)
        if existing.returncode == 0:
            raise BrowserSidecarError(
                f"foreign {kind} name already exists",
                check=f"foreign-{kind}-name",
            )
        if existing.returncode not in (1,):
            raise BrowserSidecarError(
                f"cannot prove {kind} name absence",
                check=f"{kind}-name-absence",
            )

    def _inspect_image(
        self,
        *,
        role: str,
        image_id: str,
        projections: Sequence[tuple[str, str, str]],
    ) -> None:
        """Require one exact pre-provisioned linux/amd64 reviewed image."""

        address = image_id.removeprefix("sha256:")
        for field, projection, expected in projections:
            try:
                completed = self._docker(
                    "inspect",
                    "--type=image",
                    "--format",
                    projection,
                    address,
                )
            except BrowserSidecarError as exc:
                raise BrowserSidecarError(
                    f"{role} image is unavailable",
                    check=f"{role}-image-access",
                ) from exc
            lines = completed.stdout.splitlines()
            if lines != [expected]:
                raise BrowserSidecarError(
                    f"{role} image {field} mismatch",
                    check=f"{role}-image-{field}",
                )

    def _wait_sidecar_ready(self) -> Mapping[str, Any]:
        """Wait for one exact readiness record without replacing the Sidecar."""

        if self.container_id is None:
            raise BrowserSidecarError("Sidecar was not created", check="readiness")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_cancelled()
            logs = self._docker("logs", "--tail", "50", self.container_id)
            for line in logs.stdout.splitlines():
                if not line.startswith("{"):
                    continue
                record = _strict_json(line, "readiness")
                if (
                    isinstance(record, dict)
                    and set(record) == {"event", "jobId", "endpointPath", "programs"}
                    and record.get("event") == "ready"
                    and record.get("jobId") == self.job_id
                    and record.get("programs") == PROGRAMS
                    and isinstance(record.get("endpointPath"), str)
                    and str(record["endpointPath"]).startswith("/")
                ):
                    return record
            state = self._docker(
                "container",
                "inspect",
                self.container_id,
                "--format",
                "{{json .State}}",
                check=False,
            )
            if state.returncode:
                raise BrowserSidecarError(
                    "Sidecar disappeared before readiness",
                    check="readiness-exit",
                )
            payload = _strict_json(state.stdout, "sidecar-state")
            if not isinstance(payload, dict) or payload.get("Running") is not True:
                raise BrowserSidecarError(
                    "Sidecar stopped before readiness",
                    check="readiness-exit",
                )
            time.sleep(0.1)
        raise BrowserSidecarError(
            "Sidecar readiness deadline exceeded",
            check="readiness-timeout",
        )

    def _broker_socket_identity(self) -> tuple[int, int]:
        """Require the exact private socket inode created by the Broker."""

        try:
            parent = self.capability_dir.stat()
            socket_state = self.socket_path.lstat()
        except OSError as exc:
            raise BrowserSidecarError(
                "registered-program broker socket is unavailable",
                check="broker-socket",
            ) from exc
        if (
            stat.S_IMODE(parent.st_mode) != 0o700
            or not stat.S_ISSOCK(socket_state.st_mode)
            or socket_state.st_nlink != 1
            or socket_state.st_uid != os.getuid()
            or stat.S_IMODE(socket_state.st_mode) != 0o600
        ):
            raise BrowserSidecarError(
                "registered-program broker socket identity is invalid",
                check="broker-socket",
            )
        return socket_state.st_dev, socket_state.st_ino

    def _wait_broker_ready(self) -> Mapping[str, Any]:
        """Wait for one exact Broker-container readiness record."""

        if self.broker_container_id is None:
            raise BrowserSidecarError("Broker was not created", check="broker-readiness")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_cancelled()
            logs = self._docker("logs", "--tail", "50", self.broker_container_id)
            records = [
                _strict_json(line, "broker-readiness")
                for line in logs.stdout.splitlines()
                if line.startswith("{")
            ]
            ready = next(
                (
                    record
                    for record in records
                    if isinstance(record, dict) and record.get("event") == "ready"
                ),
                None,
            )
            if ready is not None:
                record = ready
                break
            state = self._docker(
                "container",
                "inspect",
                self.broker_container_id,
                "--format",
                "{{json .State}}",
                check=False,
            )
            if state.returncode:
                raise BrowserSidecarError(
                    "Broker disappeared before readiness",
                    check="broker-readiness-exit",
                )
            payload = _strict_json(state.stdout, "broker-state")
            if not isinstance(payload, dict) or payload.get("Running") is not True:
                raise BrowserSidecarError(
                    "Broker stopped before readiness",
                    check="broker-readiness-exit",
                )
            time.sleep(0.1)
        else:
            raise BrowserSidecarError(
                "Broker readiness deadline exceeded",
                check="broker-readiness-timeout",
            )
        isolation = record.get("isolation") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or set(record)
            != {"event", "schema", "jobId", "imageId", "programs", "isolation"}
            or record.get("event") != "ready"
            or record.get("schema") != BROKER_SCHEMA
            or record.get("jobId") != self.job_id
            or record.get("imageId") != IMAGE_ID
            or record.get("programs") != PROGRAMS
            or not isinstance(isolation, dict)
            or set(isolation)
            != {"sourceAliasesVisible", "externalEgressBlocked", "browserPid"}
            or isolation.get("sourceAliasesVisible") != []
            or isolation.get("externalEgressBlocked") is not True
            or not isinstance(isolation.get("browserPid"), int)
        ):
            raise BrowserSidecarError(
                "registered-program broker identity mismatch",
                check="broker-readiness",
            )
        self.broker_readiness = record
        self.socket_identity = self._broker_socket_identity()
        return record

    def start(self) -> Path:
        """Start one exact Sidecar and publish its bounded sandbox authority."""

        try:
            self._check_cancelled()
            if (
                self.gate_artifact_sha256 is None
                or self.surface_manifest_sha256 is None
            ):
                raise BrowserSidecarError(
                    "nested Browser Gate must be sealed before Sidecar startup",
                    check="nested-gate-identity",
                )
            self.run_dir.mkdir(parents=True, exist_ok=True)
            for path, check in (
                (self.authority_path, "authority-preexisting"),
                (self.socket_path, "broker-socket-preexisting"),
                (self.nested_gate_socket_path, "nested-gate-socket-preexisting"),
            ):
                if path.exists() or path.is_symlink():
                    raise BrowserSidecarError(
                        "job-private capability path already exists",
                        check=check,
                    )
            self.docker = shutil.which("docker")
            if self.docker is None:
                raise BrowserSidecarError(
                    "Docker is required for the formal Browser Sidecar",
                    check="docker-access",
                )
        except BaseException as exc:
            self.first_error = (
                exc.check if isinstance(exc, BrowserSidecarError) else "start-unexpected"
            )
            self.close(workload_status=None)
            raise
        try:
            self._check_cancelled()
            self._inspect_image(
                role="sidecar",
                image_id=IMAGE_ID,
                projections=IMAGE_PROJECTIONS,
            )
            self._inspect_image(
                role="broker",
                image_id=BROKER_IMAGE_ID,
                projections=BROKER_IMAGE_PROJECTIONS,
            )
            self._require_absent_name("network", self.network_name)
            self._require_absent_name("container", self.container_name)
            self._require_absent_name("container", self.broker_container_name)
            self._check_cancelled()
            created = self._docker(
                "network",
                "create",
                "--internal",
                "--label",
                self.label,
                "--label",
                self.owner_label,
                self.network_name,
            )
            network_id = created.stdout.strip()
            if RESOURCE_ID.fullmatch(network_id) is None:
                raise BrowserSidecarError(
                    "created network identity is invalid",
                    check="network-id",
                )
            self.network_id = network_id
            self._check_cancelled()
            started = self._docker(
                "run",
                "-d",
                "--name",
                self.container_name,
                "--label",
                self.label,
                "--label",
                self.owner_label,
                "--network",
                self.network_name,
                "--network-alias",
                "sidecar",
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--read-only",
                "--init",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "256",
                "--memory",
                "1536m",
                "--memory-swap",
                "1536m",
                "--cpus",
                "1.5",
                "--shm-size",
                "256m",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                "--tmpfs",
                "/home/pwuser:rw,nosuid,nodev,size=64m,uid=1001,gid=1001,mode=700",
                "-e",
                f"BROWSER_SIDECAR_JOB_ID={self.job_id}",
                IMAGE_ID,
            )
            container_id = started.stdout.strip()
            if RESOURCE_ID.fullmatch(container_id) is None:
                raise BrowserSidecarError(
                    "created Sidecar identity is invalid",
                    check="container-id",
                )
            self.container_id = container_id
            self._check_cancelled()
            self.readiness = self._wait_sidecar_ready()
            self._check_cancelled()
            broker_started = self._docker(
                "run",
                "-d",
                "--name",
                self.broker_container_name,
                "--label",
                self.label,
                "--label",
                self.owner_label,
                "--network",
                self.network_name,
                "--pull=never",
                "--platform",
                "linux/amd64",
                "--read-only",
                "--init",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "64",
                "--memory",
                "384m",
                "--memory-swap",
                "384m",
                "--cpus",
                "0.5",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                (
                    "type=bind,src="
                    f"{self.capability_dir},dst=/run/meshshot-browser"
                ),
                BROKER_IMAGE_ID,
                "--job-id",
                self.job_id,
            )
            broker_container_id = broker_started.stdout.strip()
            if RESOURCE_ID.fullmatch(broker_container_id) is None:
                raise BrowserSidecarError(
                    "created Broker identity is invalid",
                    check="broker-container-id",
                )
            self.broker_container_id = broker_container_id
            self._check_cancelled()
            self._wait_broker_ready()
            self._check_cancelled()
            authority = {
                "schema": AUTHORITY_SCHEMA,
                "jobId": self.job_id,
                "gateNonce": self.gate_nonce,
                "imageId": IMAGE_ID,
                "programs": PROGRAMS,
            }
            _write_json_atomic(self.authority_path, authority, mode=0o444)
            return self.authority_path
        except BaseException as exc:
            self.first_error = (
                exc.check if isinstance(exc, BrowserSidecarError) else "start-unexpected"
            )
            self.close(workload_status=None)
            raise

    def poll_failed(self) -> bool:
        """Return whether the exact Sidecar or broker exited during workload."""

        if self.socket_identity is None:
            return True
        try:
            if self._broker_socket_identity() != self.socket_identity:
                return True
        except BrowserSidecarError:
            return True
        for role, container_id in (
            ("sidecar", self.container_id),
            ("broker", self.broker_container_id),
        ):
            if container_id is None:
                return True
            state = self._docker(
                "container",
                "inspect",
                container_id,
                "--format",
                "{{json .State}}",
                check=False,
            )
            if state.returncode:
                return True
            try:
                payload = _strict_json(state.stdout, f"{role}-state")
            except BrowserSidecarError:
                return True
            if not isinstance(payload, dict) or payload.get("Running") is not True:
                return True
        return False

    def _prove_absence(self) -> Mapping[str, object]:
        """Prove no resource with both exact job and owner labels remains."""

        errors: list[str] = []
        retained: dict[str, list[str]] = {"containers": [], "networks": []}
        for kind, key in (("container", "containers"), ("network", "networks")):
            try:
                completed = self._docker(
                    kind,
                    "ls",
                    "-a" if kind == "container" else "--no-trunc",
                    "--filter",
                    f"label={self.label}",
                    "--filter",
                    f"label={self.owner_label}",
                    "--format",
                    "{{.ID}}",
                    check=False,
                )
            except BrowserSidecarError:
                errors.append(f"{kind}-absence")
                continue
            if completed.returncode:
                errors.append(f"{kind}-absence")
            retained[key] = completed.stdout.split()
        return {
            **retained,
            "errors": errors,
            "proved": not errors and not retained["containers"] and not retained["networks"],
        }

    def close(self, *, workload_status: int | None) -> Mapping[str, object]:
        """Perform bounded reverse-order cleanup and publish terminal evidence."""

        if self._closed:
            try:
                return _strict_json(
                    self.receipt_path.read_text(encoding="utf-8"),
                    "job-receipt",
                )
            except OSError as exc:
                raise BrowserSidecarError(
                    "terminal Browser Sidecar receipt is unavailable",
                    check="receipt",
                ) from exc
        self._closed = True
        broker_status: int | None = None
        broker_terminal_state: Mapping[str, Any] | None = None
        if self.broker_container_id is not None:
            try:
                stopped = self._docker(
                    "stop",
                    "--time",
                    "10",
                    self.broker_container_id,
                    check=False,
                )
                if stopped.returncode:
                    self.cleanup_errors.append("broker-stop")
            except BrowserSidecarError:
                self.cleanup_errors.append("broker-stop")
            try:
                logs = self._docker(
                    "logs",
                    "--tail",
                    "50",
                    self.broker_container_id,
                    check=False,
                )
                if logs.returncode:
                    self.cleanup_errors.append("broker-terminal-evidence")
                else:
                    records = [
                        _strict_json(line, "broker-terminal")
                        for line in logs.stdout.splitlines()
                        if line.startswith("{")
                    ]
                    terminals = [
                        record
                        for record in records
                        if isinstance(record, dict)
                        and record.get("event") == "terminal"
                    ]
                    if len(terminals) != 1:
                        self.cleanup_errors.append("broker-terminal-evidence")
                    else:
                        terminal_record = terminals[0]
                        counts = terminal_record.get("programCounts")
                        accepted = terminal_record.get("acceptedRequests")
                        program_predicates = terminal_record.get("programPredicates")
                        if (
                            set(terminal_record)
                            != {
                                "event",
                                "schema",
                                "jobId",
                                "imageId",
                                "acceptedRequests",
                                "freshContexts",
                                "programCounts",
                                "programPredicates",
                            }
                            or terminal_record.get("schema") != BROKER_SCHEMA
                            or terminal_record.get("jobId") != self.job_id
                            or terminal_record.get("imageId") != IMAGE_ID
                            or not isinstance(accepted, int)
                            or isinstance(accepted, bool)
                            or accepted < 0
                            or terminal_record.get("freshContexts") != accepted + 1
                            or not isinstance(counts, dict)
                            or set(counts) != {"residual", "viewer"}
                            or any(
                                not isinstance(count, int)
                                or isinstance(count, bool)
                                or count < 0
                                for count in counts.values()
                            )
                            or sum(counts.values()) != accepted
                            or not isinstance(program_predicates, dict)
                            or set(program_predicates)
                            != {
                                "residualEightView",
                                "viewerProjectionChanged",
                                "viewerArtifactClean",
                            }
                            or any(
                                not isinstance(predicate, bool)
                                for predicate in program_predicates.values()
                            )
                        ):
                            self.cleanup_errors.append("broker-terminal-evidence")
                        else:
                            self.broker_terminal = terminal_record
            except BrowserSidecarError:
                self.cleanup_errors.append("broker-terminal-evidence")
            try:
                inspected = self._docker(
                    "container",
                    "inspect",
                    self.broker_container_id,
                    "--format",
                    "{{json .State}}",
                    check=False,
                )
                if inspected.returncode:
                    self.cleanup_errors.append("broker-terminal")
                else:
                    state = _strict_json(inspected.stdout, "broker-terminal-state")
                    broker_terminal_state = state if isinstance(state, dict) else None
                    broker_status = (
                        state.get("ExitCode") if isinstance(state, dict) else None
                    )
                    if broker_status != 0:
                        self.cleanup_errors.append("broker-terminal")
            except BrowserSidecarError:
                self.cleanup_errors.append("broker-terminal")
            try:
                removed = self._docker(
                    "rm",
                    "-f",
                    self.broker_container_id,
                    check=False,
                )
                if removed.returncode:
                    self.cleanup_errors.append("broker-remove")
            except BrowserSidecarError:
                self.cleanup_errors.append("broker-remove")
        terminal_state: Mapping[str, Any] | None = None
        closing_observed = False
        if self.container_id is not None:
            try:
                stopped = self._docker(
                    "stop",
                    "--time",
                    "15",
                    self.container_id,
                    check=False,
                )
            except BrowserSidecarError:
                self.cleanup_errors.append("sidecar-stop")
            else:
                if stopped.returncode:
                    self.cleanup_errors.append("sidecar-stop")
            try:
                logs = self._docker(
                    "logs",
                    "--tail",
                    "50",
                    self.container_id,
                    check=False,
                )
            except BrowserSidecarError:
                self.cleanup_errors.append("sidecar-closing")
            else:
                if logs.returncode:
                    self.cleanup_errors.append("sidecar-closing")
                else:
                    try:
                        records = [
                            _strict_json(line, "sidecar-terminal-log")
                            for line in logs.stdout.splitlines()
                            if line.startswith("{")
                        ]
                    except BrowserSidecarError:
                        self.cleanup_errors.append("sidecar-closing")
                    else:
                        closing_records = [
                            record
                            for record in records
                            if isinstance(record, dict)
                            and record.get("event") == "closing"
                        ]
                        closing_observed = closing_records == [
                            {
                                "event": "closing",
                                "jobId": self.job_id,
                                "reason": "SIGTERM",
                            }
                        ]
                        if not closing_observed:
                            self.cleanup_errors.append("sidecar-closing")
            try:
                inspected = self._docker(
                    "container",
                    "inspect",
                    self.container_id,
                    "--format",
                    "{{json .State}}",
                    check=False,
                )
            except BrowserSidecarError:
                self.cleanup_errors.append("sidecar-terminal")
            else:
                if inspected.returncode:
                    self.cleanup_errors.append("sidecar-terminal")
                else:
                    try:
                        payload = _strict_json(inspected.stdout, "sidecar-terminal")
                        terminal_state = payload if isinstance(payload, dict) else None
                    except BrowserSidecarError:
                        self.cleanup_errors.append("sidecar-terminal")
            try:
                removed = self._docker(
                    "rm",
                    "-f",
                    self.container_id,
                    check=False,
                )
            except BrowserSidecarError:
                self.cleanup_errors.append("container-remove")
            else:
                if removed.returncode:
                    self.cleanup_errors.append("container-remove")
        if self.network_id is not None:
            try:
                removed = self._docker(
                    "network",
                    "rm",
                    self.network_id,
                    check=False,
                )
            except BrowserSidecarError:
                self.cleanup_errors.append("network-remove")
            else:
                if removed.returncode:
                    self.cleanup_errors.append("network-remove")
        absence = (
            self._prove_absence()
            if self.docker is not None
            else {
                "containers": [],
                "networks": [],
                "errors": [],
                "proved": (
                    self.network_id is None
                    and self.container_id is None
                    and self.broker_container_id is None
                ),
            }
        )
        if absence.get("containers") or absence.get("networks"):
            self.cleanup_errors.append("retained-resource")
        elif absence.get("proved") is not True:
            self.cleanup_errors.append("absence-proof")
        try:
            self.authority_path.unlink(missing_ok=True)
        except OSError:
            self.cleanup_errors.append("authority-remove")
        if self.socket_path.exists() or self.socket_path.is_symlink():
            try:
                current = self.socket_path.lstat()
                current_identity = (current.st_dev, current.st_ino)
                if self.socket_identity is None or current_identity != self.socket_identity:
                    self.cleanup_errors.append("socket-identity")
                else:
                    self.socket_path.unlink()
            except OSError:
                self.cleanup_errors.append("socket-remove")
        for path, identity in self.gate_file_identities.items():
            try:
                metadata = path.lstat()
                if (metadata.st_dev, metadata.st_ino) != identity:
                    self.cleanup_errors.append("nested-gate-file-identity")
                else:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                self.cleanup_errors.append("nested-gate-file-remove")
        try:
            self.capability_dir.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            self.cleanup_errors.append("capability-dir-remove")
        terminal_record = self.broker_terminal or {}
        accepted = terminal_record.get("acceptedRequests")
        fresh_contexts = terminal_record.get("freshContexts")
        program_counts = terminal_record.get("programCounts")
        program_predicates = terminal_record.get("programPredicates")
        counts_valid = (
            isinstance(accepted, int)
            and not isinstance(accepted, bool)
            and accepted >= 0
            and isinstance(fresh_contexts, int)
            and not isinstance(fresh_contexts, bool)
            and fresh_contexts >= 1
            and isinstance(program_counts, dict)
            and set(program_counts) == {"residual", "viewer"}
            and all(
                isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
                for count in program_counts.values()
            )
        )
        nested = self.nested_gate_predicates or {}
        predicates = {
            "sidecarReady": self.readiness is not None,
            "brokerReady": self.broker_readiness is not None,
            "sidecarSourceHidden": (
                isinstance(self.broker_readiness, dict)
                and isinstance(self.broker_readiness.get("isolation"), dict)
                and self.broker_readiness["isolation"].get("sourceAliasesVisible") == []
            ),
            "sidecarEgressBlocked": (
                isinstance(self.broker_readiness, dict)
                and isinstance(self.broker_readiness.get("isolation"), dict)
                and self.broker_readiness["isolation"].get("externalEgressBlocked") is True
            ),
            "socketFixed": self.socket_identity is not None,
            "brokerResidualAccepted": (
                counts_valid and program_counts.get("residual", 0) >= 1
            ),
            "brokerResidualEightView": (
                isinstance(program_predicates, dict)
                and program_predicates.get("residualEightView") is True
            ),
            "brokerViewerAccepted": (
                counts_valid and program_counts.get("viewer", 0) >= 1
            ),
            "brokerViewerProjectionChanged": (
                isinstance(program_predicates, dict)
                and program_predicates.get("viewerProjectionChanged") is True
            ),
            "brokerViewerArtifactClean": (
                isinstance(program_predicates, dict)
                and program_predicates.get("viewerArtifactClean") is True
            ),
            "brokerFreshContextsExact": (
                counts_valid
                and accepted == sum(program_counts.values())
                and fresh_contexts == accepted + 1
            ),
            "nestedPublicResidualParity": nested.get("publicResidualParity") is True,
            "nestedViewerProjectionChanged": (
                nested.get("viewerProjectionChanged") is True
            ),
            "nestedViewerArtifactClean": nested.get("viewerArtifactClean") is True,
            "nestedBrowserInventoryEmpty": (
                nested.get("browserInventoryEmpty") is True
            ),
            "nestedBrowserProcessZero": nested.get("browserProcessZero") is True,
            "brokerTerminalZero": (
                isinstance(broker_status, int)
                and not isinstance(broker_status, bool)
                and broker_status == 0
                and isinstance(broker_terminal_state, dict)
                and isinstance(broker_terminal_state.get("ExitCode"), int)
                and not isinstance(broker_terminal_state.get("ExitCode"), bool)
                and broker_terminal_state["ExitCode"] == 0
            ),
            "sidecarClosingExact": closing_observed,
            "sidecarTerminalZero": (
                isinstance(terminal_state, dict)
                and isinstance(terminal_state.get("ExitCode"), int)
                and not isinstance(terminal_state.get("ExitCode"), bool)
                and terminal_state["ExitCode"] == 0
            ),
            "workloadTerminalZero": workload_status == 0,
            "absenceProved": absence.get("proved") is True,
        }
        failure_by_predicate = {
            "sidecarReady": "sidecar-readiness",
            "brokerReady": "broker-readiness",
            "sidecarSourceHidden": "sidecar-source-alias",
            "sidecarEgressBlocked": "sidecar-external-egress",
            "socketFixed": "broker-socket",
            "brokerResidualAccepted": "residual-required",
            "brokerResidualEightView": "residual-eight-view",
            "brokerViewerAccepted": "viewer-required",
            "brokerViewerProjectionChanged": "viewer-projection",
            "brokerViewerArtifactClean": "viewer-artifact",
            "brokerFreshContextsExact": "fresh-contexts",
            "nestedPublicResidualParity": "nested-public-parity",
            "nestedViewerProjectionChanged": "nested-viewer-projection",
            "nestedViewerArtifactClean": "nested-viewer-artifact",
            "nestedBrowserInventoryEmpty": "nested-browser-inventory",
            "nestedBrowserProcessZero": "nested-browser-process",
            "brokerTerminalZero": "broker-terminal",
            "sidecarClosingExact": "sidecar-closing",
            "sidecarTerminalZero": "sidecar-terminal",
            "workloadTerminalZero": "workload-terminal",
            "absenceProved": "retained-resource",
        }
        cleanup_failure = (
            "retained-resource"
            if "retained-resource" in self.cleanup_errors
            else (self.cleanup_errors[0] if self.cleanup_errors else None)
        )
        predicate_failure = next(
            (
                failure_by_predicate[name]
                for name, passed in predicates.items()
                if not passed
            ),
            None,
        )
        failure_check = cleanup_failure or self.first_error or predicate_failure
        succeeded = failure_check is None and all(predicates.values())
        counts = {
            "acceptedRequests": accepted if counts_valid else 0,
            "freshContexts": fresh_contexts if counts_valid else 0,
            "programCounts": (
                dict(program_counts)
                if counts_valid
                else {"residual": 0, "viewer": 0}
            ),
        }
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "succeeded" if succeeded else "failed",
            "imageId": IMAGE_ID,
            "imageSourceRevision": IMAGE_SOURCE_REVISION,
            "brokerImageId": BROKER_IMAGE_ID,
            "brokerImageSourceRevision": BROKER_IMAGE_SOURCE_REVISION,
            "brokerBaseImageId": BROKER_BASE_IMAGE_ID,
            "programs": PROGRAMS,
            "predicates": predicates,
            "counts": counts,
            "failureCheck": failure_check,
            "retryAllowed": False,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.receipt_path, receipt)
        return receipt


def run_broker(args: argparse.Namespace) -> int:
    """Serve exact registered requests over one job-private Unix socket."""

    socket_path = SANDBOX_SOCKET_PATH
    if JOB_ID.fullmatch(args.job_id) is None:
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 2

    closing = False

    def request_close(signum: int, frame: object) -> None:
        """Request broker-loop termination without changing request content."""

        del signum, frame
        nonlocal closing
        closing = True

    previous = {
        signum: signal.signal(signum, request_close)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    server: socket.socket | None = None
    if socket_path.exists() or socket_path.is_symlink():
        return 2
    try:
        with urlopen("http://sidecar:3001/v1/authority", timeout=15) as response:
            if response.status != 200:
                return 1
            raw_authority = response.read(16 * 1024 + 1)
        if len(raw_authority) > 16 * 1024:
            return 1
        authority = _strict_json(raw_authority.decode("ascii"), "sidecar-authority")
        if (
            not isinstance(authority, dict)
            or set(authority)
            != {
                "schema",
                "jobId",
                "endpointPath",
                "browserPid",
                "chromiumRevision",
                "chromiumVersion",
                "playwrightVersion",
                "programs",
                "sourceAliasesVisible",
            }
            or authority.get("schema") != "meshshot.browser-sidecar.prototype/1"
            or authority.get("jobId") != args.job_id
            or authority.get("programs") != PROGRAMS
            or not isinstance(authority.get("endpointPath"), str)
            or not authority["endpointPath"].startswith("/browser/")
        ):
            return 1
        browser_endpoint = f"ws://sidecar:3000{authority['endpointPath']}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect(
                browser_endpoint,
                timeout=15_000,
            )
            try:
                broker = RegisteredProgramBroker(browser, args.job_id)
                isolation = broker.preflight()
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(socket_path))
                os.chmod(socket_path, 0o600)
                server.listen(4)
                server.settimeout(0.2)
                print(
                    json.dumps(
                        {
                            "event": "ready",
                            "schema": BROKER_SCHEMA,
                            "jobId": args.job_id,
                            "imageId": IMAGE_ID,
                            "programs": PROGRAMS,
                            "isolation": isolation,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                while not closing:
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        connection.settimeout(120)
                        raw = bytearray()
                        try:
                            while True:
                                chunk = connection.recv(65536)
                                if not chunk:
                                    break
                                raw.extend(chunk)
                                if len(raw) > MAX_REQUEST_BYTES + 1:
                                    raise BrowserSidecarError(
                                        "render request is too large",
                                        check="render-request-size",
                                    )
                            if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
                                raise BrowserSidecarError(
                                    "render request framing is invalid",
                                    check="render-request-framing",
                                )
                            request = _strict_json(
                                bytes(raw[:-1]).decode("utf-8"),
                                "render-request",
                            )
                            response = broker.execute(request)
                        except (BrowserSidecarError, UnicodeDecodeError) as exc:
                            response = {
                                "schema": "meshshot.browser-sidecar.render-error/1",
                                "jobId": args.job_id,
                                "imageId": IMAGE_ID,
                                "program": None,
                                "error": {
                                    "classification": (
                                        exc.check
                                        if isinstance(exc, BrowserSidecarError)
                                        else "render-request-encoding"
                                    )
                                },
                            }
                        connection.sendall(
                            json.dumps(
                                response,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("ascii")
                            + b"\n"
                        )
                print(
                    json.dumps(
                        {
                            "event": "terminal",
                            "schema": BROKER_SCHEMA,
                            "jobId": args.job_id,
                            "imageId": IMAGE_ID,
                            "acceptedRequests": broker.request_count,
                            "freshContexts": broker.request_count + 1,
                            "programCounts": broker.program_counts,
                            "programPredicates": broker.program_predicates,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            finally:
                browser.close()
    except (OSError, Exception):
        return 1
    finally:
        if server is not None:
            server.close()
        socket_path.unlink(missing_ok=True)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the fixed outer-only broker command."""

    parser = argparse.ArgumentParser(description="Browser Sidecar lifecycle helper")
    subparsers = parser.add_subparsers(dest="action", required=True)
    broker = subparsers.add_parser("broker-container")
    broker.add_argument("--job-id", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the fixed registered-program broker action."""

    args = parse_args(argv)
    if args.action == "broker-container":
        return run_broker(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
