"""Trusted Step 0 evidence provider seam and its production implementation.

The seam has one small typed shape:

* ``StepZeroEvidenceRequest`` bundles the read-only Canonical Reference
  directory, the read-only ingested candidate mesh, the two W1-owned
  opaque stage directories the provider must populate, and the closed
  preview profile identity the Workspace already committed to.
* ``StepZeroEvidenceProvider`` is the callable that consumes the request
  and writes canonical measurement and formal preview bytes into the
  stage.  The request never carries a Workspace authority path; the
  runner binds the fixed shipped package roots privately.

The production provider (:func:`real_step_zero_evidence_provider`) is
runner-assembled and fixed; the Agent cannot configure or replace it.
It reuses the shipped canonical CAD conversion, measurement, and preview
programs from ``meshscope.voxblame`` and ``meshshot`` — no geometry
algorithm is reimplemented.  The formal preview is produced by the
canonical Browser Runtime ``render_residual_preview`` renderer; only the
macOS test seam may substitute a bounded fake renderer, and the Linux
Browser Runtime remains the gate for a real production claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Protocol


class StepZeroEvidenceError(RuntimeError):
    """Trusted provider failure with a closed classification."""

    def __init__(self, classification: str, detail: str = ""):
        self.classification = classification
        self.detail = detail
        super().__init__(f"{classification}: {detail}" if detail else classification)


@dataclass(frozen=True)
class StepZeroEvidenceRequest:
    """One request from W1 to the trusted Step 0 evidence provider.

    All paths are Workspace-facade-owned.  The provider must:
      * Read only ``canonical_reference`` and ``candidate_mesh``.
      * Write only into ``voxblame_output`` and ``preview_output``.
      * Not interpret any path's location relative to Workspace authority.

    ``preview_profile`` is the closed ``{name, sha256}`` identity value
    the Workspace has already committed for the experiment.
    """

    canonical_reference: Path
    candidate_mesh: Path
    voxblame_output: Path
    preview_output: Path
    preview_profile: Mapping[str, Any]


class StepZeroEvidenceProvider(Protocol):
    """The fixed W1 provider seam used to produce Step 0 evidence."""

    def __call__(self, request: StepZeroEvidenceRequest) -> None: ...


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_PACKAGES = _REPO_ROOT / "skills/mesh-compare/scripts/packages"
_MESHSCOPE_SRC = _SHIPPED_PACKAGES / "meshscope/src"
_MESHSHOT_SRC = _SHIPPED_PACKAGES / "meshshot/src"


def _ensure_shipped_package(package_root: Path, package_name: str) -> Path:
    """Import one package from its fixed vendored skill runtime."""

    if not (package_root / package_name / "__init__.py").is_file():
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} is missing from the shipped tool subset",
        )
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    try:
        package = importlib.import_module(package_name)
    except ImportError as error:
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} is not importable from the shipped tool subset: {error}",
        ) from error
    origin = getattr(package, "__file__", None)
    try:
        Path(origin).resolve().relative_to(package_root.resolve())
    except (TypeError, ValueError) as error:
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} resolved outside the shipped tool subset",
        ) from error
    return package_root


def _import_meshscope(meshscope_src: Path | None = None):
    """Import the shipped meshscope.voxblame canonical measurement API."""

    _ensure_shipped_package(meshscope_src or _MESHSCOPE_SRC, "meshscope")
    from meshscope.voxblame import (  # type: ignore
        measure_step,
        prepare_preview_scene,
        publish_preview,
        validate_preview_identity,
    )
    return measure_step, prepare_preview_scene, publish_preview, validate_preview_identity


def _import_meshshot(meshshot_src: Path | None = None):
    """Import the shipped meshshot canonical Browser Runtime renderer."""

    _ensure_shipped_package(meshshot_src or _MESHSHOT_SRC, "meshshot")
    from meshshot import (  # type: ignore
        MeshGeometry,
        load_profile,
        render_residual_preview,
    )
    return MeshGeometry, load_profile, render_residual_preview


def real_step_zero_evidence_provider(
    request: StepZeroEvidenceRequest,
    *,
    renderer: Callable[..., Any] | None = None,
    meshscope_src: Path | None = None,
    meshshot_src: Path | None = None,
) -> None:
    """Production Step 0 evidence provider.

    Uses the canonical shared implementations without reimplementing any
    geometry algorithm:

      1. ``meshscope.voxblame.measure_step`` populates the caller-supplied
         ``voxblame_output`` directory (session/reference/step summary).
      2. ``meshscope.voxblame.prepare_preview_scene`` +
         ``validate_preview_identity`` build the closed preview scene and
         identity bound to the Workspace's committed preview profile.
      3. ``meshshot.render_residual_preview`` renders the eight-view PNG
         through the Browser Runtime.  A ``renderer`` override supports
         tests; the production runner never supplies one.
      4. ``meshscope.voxblame.publish_preview`` writes ``preview.json``
         and ``preview.png`` into the caller-supplied ``preview_output``
         directory.
    """

    (
        measure_step,
        prepare_preview_scene,
        publish_preview,
        validate_preview_identity,
    ) = _import_meshscope(meshscope_src)
    MeshGeometry, load_profile, render_residual_preview = _import_meshshot(meshshot_src)

    if renderer is None:
        renderer = render_residual_preview

    try:
        measurement = measure_step(
            request.canonical_reference,
            request.candidate_mesh,
            request.voxblame_output,
            step=0,
            compare_to=None,
            backend="python",
        )
    except Exception as exc:
        raise StepZeroEvidenceError("measurement_failed", str(exc)) from exc
    if measurement is None:
        raise StepZeroEvidenceError("measurement_failed", "canonical measurement did not return a result")

    try:
        loaded_profile = load_profile()
    except Exception as exc:
        raise StepZeroEvidenceError("preview_failed", f"preview profile unavailable: {exc}") from exc
    if loaded_profile.profile["name"] != request.preview_profile.get("name") or (
        loaded_profile.sha256 != request.preview_profile.get("sha256")
    ):
        raise StepZeroEvidenceError(
            "preview_profile_mismatch",
            "installed preview profile identity differs from the Workspace's committed profile",
        )

    try:
        scene = prepare_preview_scene(request.canonical_reference, request.candidate_mesh)
        identity = validate_preview_identity(
            scene,
            profile_name=loaded_profile.profile["name"],
            profile_sha256=loaded_profile.sha256,
            experiment_profile=dict(request.preview_profile),
            variant="step",
            selected_step=None,
            selected_summary=None,
            selected_summary_sha256=None,
        )
        rendered = renderer(
            MeshGeometry(**scene.reference_geometry),
            MeshGeometry(**scene.candidate_geometry),
            variant="step",
            exterior_directions=scene.exterior.exact["outside_directions"],
        )
    except Exception as exc:
        raise StepZeroEvidenceError("preview_failed", str(exc)) from exc

    if rendered.variant != "step" or rendered.profile_sha256 != loaded_profile.sha256:
        raise StepZeroEvidenceError(
            "preview_failed", "renderer profile identity conflict"
        )

    try:
        publish_preview(
            scene,
            png_bytes=rendered.png_bytes,
            output=request.preview_output,
            profile=loaded_profile.profile,
            ordered_views=[dict(view) for view in rendered.views],
            identity=identity,
        )
    except Exception as exc:
        raise StepZeroEvidenceError("preview_failed", str(exc)) from exc


__all__ = [
    "StepZeroEvidenceError",
    "StepZeroEvidenceProvider",
    "StepZeroEvidenceRequest",
    "real_step_zero_evidence_provider",
]
