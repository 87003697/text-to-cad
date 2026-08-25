"""Trusted Repair evidence provider seam and its production implementation.

The seam mirrors the Step 0 shape: one closed typed request the runner
assembles, and one callable that consumes it.  The provider is
runner-assembled and fixed; the Agent Surface cannot register or replace
it.

Given an external stage populated by W1 with descriptor-safe copies of

  * the canonical reference directory,
  * the ingested candidate mesh and the candidate source tree,
  * the parent Measured Step's voxblame subtree (session, reference
    tree, and parent step directory) and the parent selected candidate
    source tree,
  * the current Attempt's plan document and the parent identity facts,

the production provider

  1. runs the canonical ``meshscope.voxblame.measure_step`` update to
     produce the new Measured Step evidence in the same voxblame layout,
  2. reuses the canonical ``meshscope.voxblame.publish_region_diff`` to
     compute the Region Diff for the exact repair batch,
  3. renders the formal preview through the canonical Browser Runtime
     ``meshshot.render_residual_preview`` and writes it through
     ``meshscope.voxblame.publish_preview``, and
  4. writes an objective source-change delta from the parent and
     candidate source subtrees.

The Region Diff, source-change delta, measurement, and preview are all
derived from bytes W1 hydrated into the private external stage; the
provider never observes any Workspace-relative path and never sees the
semantic Agent assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Callable, Mapping, Protocol


try:
    from scripts.pilot.step_zero_evidence import (  # type: ignore
        StepZeroEvidenceError,
        _ensure_shipped_package,
        _MESHSCOPE_SRC,
        _MESHSHOT_SRC,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from step_zero_evidence import (  # type: ignore
        StepZeroEvidenceError,
        _ensure_shipped_package,
        _MESHSCOPE_SRC,
        _MESHSHOT_SRC,
    )


SOURCE_CHANGES_SCHEMA = "mesh-to-cad.source-changes/1"


class RepairEvidenceError(RuntimeError):
    """Trusted repair provider failure with a closed classification."""

    def __init__(self, classification: str, detail: str = ""):
        self.classification = classification
        self.detail = detail
        super().__init__(f"{classification}: {detail}" if detail else classification)


@dataclass(frozen=True)
class RepairEvidenceRequest:
    """One request from W1 to the trusted Repair evidence provider.

    Every path lives in a W1-owned external stage outside the Workspace
    tree.  The provider must:

      * Read only the canonical reference directory, the candidate mesh
        and source subtree, and the parent voxblame/source subtrees.
      * Write only into ``voxblame_output``, ``preview_output``,
        ``region_diff_output`` and ``source_changes_output``.
      * Not interpret path locations relative to Workspace authority.

    ``preview_profile`` is the closed ``{name, sha256}`` identity value
    the Workspace committed to for the experiment.  ``plan`` is a copy
    of the active Attempt's plan document — never re-authored by the
    provider.  ``from_step``, ``to_step``, ``parent_observable_sha256``
    and ``parent_selected_summary_sha256`` are the parent-binding facts
    W1 already committed to the Workspace.
    """

    canonical_reference: Path
    candidate_mesh: Path
    candidate_source: Path
    parent_voxblame: Path
    parent_source: Path
    voxblame_output: Path
    preview_output: Path
    region_diff_output: Path
    source_changes_output: Path
    plan: Mapping[str, Any]
    preview_profile: Mapping[str, Any]
    from_step: int
    to_step: int
    parent_observable_sha256: str
    parent_selected_summary_sha256: str


class RepairEvidenceProvider(Protocol):
    """Fixed W1 seam that produces trusted Repair Cycle evidence."""

    def __call__(self, request: RepairEvidenceRequest) -> None: ...


def _import_meshscope():
    """Import the shipped ``meshscope.voxblame`` canonical Repair API."""

    _ensure_shipped_package(_MESHSCOPE_SRC, "meshscope")
    from meshscope.voxblame import (  # type: ignore
        measure_step,
        prepare_preview_scene,
        publish_preview,
        publish_region_diff,
        validate_preview_identity,
    )
    return (
        measure_step,
        prepare_preview_scene,
        publish_preview,
        publish_region_diff,
        validate_preview_identity,
    )


def _import_meshshot():
    """Import the shipped ``meshshot`` Browser Runtime renderer."""

    _ensure_shipped_package(_MESHSHOT_SRC, "meshshot")
    from meshshot import (  # type: ignore
        MeshGeometry,
        load_profile,
        render_residual_preview,
    )
    return MeshGeometry, load_profile, render_residual_preview


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _collect_source_digests(root: Path) -> dict[str, str]:
    """Return a mapping of POSIX-relative paths to canonical SHA-256.

    Fail-closed for symlinks or non-regular files.  Reads bytes only —
    never interprets the source tree's semantic content.
    """

    if not root.is_dir() or root.is_symlink():
        return {}
    result: dict[str, str] = {}
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        for name in filenames:
            entry = directory / name
            if entry.is_symlink():
                raise RepairEvidenceError(
                    "source_changes_failed",
                    "source tree contains a symlink",
                )
            if not entry.is_file():
                raise RepairEvidenceError(
                    "source_changes_failed",
                    "source tree contains a non-regular file",
                )
            relative = PurePosixPath(entry.resolve().relative_to(root_resolved))
            result[relative.as_posix()] = _file_sha256(entry)
    return result


def _write_source_changes(
    *,
    parent_source: Path,
    candidate_source: Path,
    from_step: int,
    to_step: int,
    output: Path,
) -> None:
    """Derive the objective source-change delta between parent and candidate.

    The delta lists every file whose byte-for-byte SHA-256 changed
    between the parent selected candidate source tree and the current
    candidate source tree.  Deleted files carry ``after_sha256=null``;
    added files carry ``before_sha256=null``.  The provider never
    interprets file contents.
    """

    before = _collect_source_digests(parent_source)
    after = _collect_source_digests(candidate_source)
    files: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        prior = before.get(path)
        current = after.get(path)
        if prior == current:
            continue
        files.append(
            {"path": path, "before_sha256": prior, "after_sha256": current}
        )
    if not files:
        raise RepairEvidenceError(
            "source_changes_failed",
            "no source-change edges between parent and candidate",
        )
    document = {
        "schema": SOURCE_CHANGES_SCHEMA,
        "from_step": from_step,
        "to_step": to_step,
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n",
        encoding="utf-8",
    )


def _hydrate_voxblame_stage(
    *, parent_voxblame: Path, voxblame_output: Path
) -> None:
    """Copy the parent voxblame subtree into the stage before ``measure_step``.

    ``meshscope.voxblame.measure_step`` requires the target output
    directory to already contain a canonical session for any non-zero
    step.  W1 hydrated the parent voxblame subtree into the stage; here
    we mirror it into the output so ``measure_step`` can validate the
    existing session and publish the new step alongside it.
    """

    import shutil

    if voxblame_output.exists():
        for child in voxblame_output.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        voxblame_output.mkdir(parents=True, exist_ok=True)
    for child in parent_voxblame.iterdir():
        if child.is_symlink():
            raise RepairEvidenceError(
                "measurement_failed",
                "parent voxblame subtree contains a symlink",
            )
        target = voxblame_output / child.name
        if child.is_dir():
            shutil.copytree(child, target, symlinks=False)
        else:
            shutil.copy2(child, target)


def real_repair_evidence_provider(
    request: RepairEvidenceRequest,
    *,
    renderer: Callable[..., Any] | None = None,
) -> None:
    """Production Repair evidence provider.

    Composes the canonical shipped implementations without
    reimplementing any geometry algorithm.
    """

    (
        measure_step,
        prepare_preview_scene,
        publish_preview,
        publish_region_diff,
        validate_preview_identity,
    ) = _import_meshscope()
    MeshGeometry, load_profile, render_residual_preview = _import_meshshot()

    if renderer is None:
        renderer = render_residual_preview

    _hydrate_voxblame_stage(
        parent_voxblame=request.parent_voxblame,
        voxblame_output=request.voxblame_output,
    )

    try:
        measurement = measure_step(
            request.canonical_reference,
            request.candidate_mesh,
            request.voxblame_output,
            step=request.to_step,
            compare_to=request.from_step,
            backend="python",
        )
    except Exception as exc:
        raise RepairEvidenceError("measurement_failed", str(exc)) from exc
    if measurement is None:
        raise RepairEvidenceError(
            "measurement_failed", "canonical measurement did not return a result"
        )

    try:
        publish_region_diff(
            request.voxblame_output,
            from_step=request.from_step,
            to_step=request.to_step,
            repair_plan=dict(request.plan),
            output=request.region_diff_output,
        )
    except Exception as exc:
        raise RepairEvidenceError("region_diff_failed", str(exc)) from exc

    try:
        loaded_profile = load_profile()
    except Exception as exc:
        raise RepairEvidenceError(
            "preview_failed", f"preview profile unavailable: {exc}"
        ) from exc
    if loaded_profile.profile["name"] != request.preview_profile.get("name") or (
        loaded_profile.sha256 != request.preview_profile.get("sha256")
    ):
        raise RepairEvidenceError(
            "preview_profile_mismatch",
            "installed preview profile identity differs from the Workspace's committed profile",
        )

    try:
        scene = prepare_preview_scene(
            request.canonical_reference, request.candidate_mesh
        )
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
        raise RepairEvidenceError("preview_failed", str(exc)) from exc

    if rendered.variant != "step" or rendered.profile_sha256 != loaded_profile.sha256:
        raise RepairEvidenceError(
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
        raise RepairEvidenceError("preview_failed", str(exc)) from exc

    _write_source_changes(
        parent_source=request.parent_source,
        candidate_source=request.candidate_source,
        from_step=request.from_step,
        to_step=request.to_step,
        output=request.source_changes_output,
    )


__all__ = [
    "RepairEvidenceError",
    "RepairEvidenceProvider",
    "RepairEvidenceRequest",
    "SOURCE_CHANGES_SCHEMA",
    "real_repair_evidence_provider",
]
