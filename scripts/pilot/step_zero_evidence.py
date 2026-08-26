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

from contextlib import contextmanager
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
import threading
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


_SHIPPED_IMPORT_LOCK = threading.RLock()
# A package family may be reloaded only after this helper authenticated its
# current root; unknown ambient modules remain fail-closed.
_SHIPPED_PACKAGE_ROOTS: dict[str, Path] = {}


@contextmanager
def _shipped_import_transaction():
    """Keep trusted imports from writing bytecode into the shipped tree."""

    # ``importlib`` consults this process-global flag while loading source.
    # Serialize only these short transactions and restore the caller's value
    # so concurrent provider work cannot inherit a changed import policy.
    with _SHIPPED_IMPORT_LOCK:
        previous_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            yield
        finally:
            sys.dont_write_bytecode = previous_dont_write_bytecode


def _package_modules(package_name: str) -> dict[str, object]:
    """Return one top-level package family without touching other modules."""

    prefix = f"{package_name}."
    return {
        name: module
        for name, module in sys.modules.items()
        if name == package_name or name.startswith(prefix)
    }


def _package_modules_are_from_root(
    modules: Mapping[str, object], package_root: Path, package_name: str
) -> bool:
    package_directory = package_root / package_name
    for module in modules.values():
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            return False
        try:
            Path(origin).resolve().relative_to(package_directory)
        except (OSError, ValueError):
            return False
    return True


def _evict_package_modules(package_name: str) -> None:
    for name in _package_modules(package_name):
        sys.modules.pop(name, None)


def _restore_package_import_state(
    package_name: str,
    modules: Mapping[str, object],
    path: list[str],
    roots: Mapping[str, Path],
) -> None:
    _evict_package_modules(package_name)
    sys.modules.update(modules)
    sys.path[:] = path
    _SHIPPED_PACKAGE_ROOTS.clear()
    _SHIPPED_PACKAGE_ROOTS.update(roots)


def _ensure_shipped_package(package_root: Path, package_name: str) -> Path:
    """Import one package from its fixed vendored skill runtime."""

    if not (package_root / package_name / "__init__.py").is_file():
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} is missing from the shipped tool subset",
        )
    resolved_root = package_root.resolve()
    loaded_modules = _package_modules(package_name)
    previous_root = _SHIPPED_PACKAGE_ROOTS.get(package_name)
    if loaded_modules:
        if previous_root is None or not _package_modules_are_from_root(
            loaded_modules, previous_root, package_name
        ):
            raise StepZeroEvidenceError(
                "provider_dependency_missing",
                f"{package_name} resolved outside the shipped tool subset",
            )
    switching_root = bool(loaded_modules and previous_root != resolved_root)
    previous_path = sys.path[:]
    previous_roots = _SHIPPED_PACKAGE_ROOTS.copy()
    package_path = str(package_root)
    path_changed = package_path not in sys.path or sys.path.index(package_path) != 0
    if switching_root:
        _evict_package_modules(package_name)
    if path_changed:
        sys.path[:] = [package_path] + [
            path for path in sys.path if path != package_path
        ]

    try:
        with _shipped_import_transaction():
            package = importlib.import_module(package_name)
    except ImportError as error:
        _restore_package_import_state(
            package_name, loaded_modules, previous_path, previous_roots
        )
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} is not importable from the shipped tool subset: {error}",
        ) from error
    except BaseException:
        _restore_package_import_state(
            package_name, loaded_modules, previous_path, previous_roots
        )
        raise
    origin = getattr(package, "__file__", None)
    try:
        Path(origin).resolve().relative_to(resolved_root / package_name)
        if not _package_modules_are_from_root(
            _package_modules(package_name), resolved_root, package_name
        ):
            raise ValueError("package family resolved outside shipped root")
    except (TypeError, ValueError) as error:
        _restore_package_import_state(
            package_name, loaded_modules, previous_path, previous_roots
        )
        raise StepZeroEvidenceError(
            "provider_dependency_missing",
            f"{package_name} resolved outside the shipped tool subset",
        ) from error
    _SHIPPED_PACKAGE_ROOTS[package_name] = resolved_root
    return package_root


@contextmanager
def _shipped_package_import(package_root: Path, package_name: str):
    """Import package symbols without mutating the digest-bound source tree."""

    with _shipped_import_transaction():
        previous_modules = _package_modules(package_name)
        previous_path = sys.path[:]
        previous_roots = _SHIPPED_PACKAGE_ROOTS.copy()
        try:
            imported_root = _ensure_shipped_package(package_root, package_name)
            yield imported_root
            if not _package_modules_are_from_root(
                _package_modules(package_name),
                Path(imported_root).resolve(),
                package_name,
            ):
                raise StepZeroEvidenceError(
                    "provider_dependency_missing",
                    f"{package_name} resolved outside the shipped tool subset",
                )
        except BaseException:
            _restore_package_import_state(
                package_name, previous_modules, previous_path, previous_roots
            )
            raise


def _import_meshscope(meshscope_src: Path | None = None):
    """Import the shipped meshscope.voxblame canonical measurement API."""

    with _shipped_package_import(meshscope_src or _MESHSCOPE_SRC, "meshscope"):
        from meshscope.voxblame import (  # type: ignore
            measure_step,
            prepare_preview_scene,
            publish_preview,
            validate_preview_identity,
        )
    return measure_step, prepare_preview_scene, publish_preview, validate_preview_identity


def _import_meshshot(meshshot_src: Path | None = None):
    """Import the shipped meshshot canonical Browser Runtime renderer."""

    with _shipped_package_import(meshshot_src or _MESHSHOT_SRC, "meshshot"):
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
