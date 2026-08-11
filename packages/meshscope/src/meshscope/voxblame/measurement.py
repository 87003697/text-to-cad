"""Measure and atomically publish canonical VoxBlame candidate steps."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
import uuid

import numpy as np
import trimesh

from meshscope.voxblame.codec import (
    STORAGE_SCHEMA,
    read_surface_tree,
    write_surface_tree,
)
from meshscope.voxblame.canonical_artifacts import (
    load_canonical_reference,
    load_mesh_bytes,
    read_artifact_bytes,
)
from meshscope.voxblame.contracts import (
    BOUNDARY_EPSILON,
    COORDINATE_CONTRACT,
    MAX_DEPTH,
    MEASUREMENT_SCHEMA,
    validate_session_contract,
)
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.exterior import (
    EXTERIOR_SNAPSHOT_SCHEMA,
    ExteriorMeasurement,
    measure_exterior_surface,
    validate_exterior_measurement,
)
from meshscope.voxblame.frame import CanonicalFrame, mesh_vertices
from meshscope.voxblame.targets import (
    partition_repair_targets,
    repair_target_page,
)
from meshscope.voxblame.tree import SurfaceTree, tree_from_codes
from meshscope.voxblame.voxelize import (
    Backend,
    backend_identity,
    build_lattice_tree,
    voxelize_mesh,
)


MEASUREMENT_SUMMARY_SCHEMA = "voxblame.summary/1"
_SURFACE_PROFILE = "conservative_surface_occupancy/1"
_TARGET_PROFILE = "repair_target_partition/1"
_EXTERIOR_PROFILE = "signed_exterior_surface/1"


@dataclass(frozen=True)
class MeasureStepResult:
    """Published compact summary and whether an identical step was reused."""

    summary: dict[str, Any]
    idempotent: bool
    backend: dict[str, str]


@dataclass(frozen=True)
class _StepArtifacts:
    candidate_tree: SurfaceTree
    missing_tree: SurfaceTree
    excess_tree: SurfaceTree
    exterior_bytes: bytes
    target_mask_bytes: dict[str, bytes]
    measurement: dict[str, Any]
    summary: dict[str, Any]


def measure_step(
    canonical_reference: str | Path,
    candidate_mesh: str | Path,
    output: str | Path,
    *,
    step: int,
    compare_to: int | None = None,
    backend: Backend = "native",
) -> MeasureStepResult:
    """Measure one canonical candidate and publish a canonical Measured Step."""

    _validate_ancestry(step, compare_to)
    reference_root = Path(canonical_reference)
    candidate_path = Path(candidate_mesh)
    output_root = Path(output)
    manifest, normalization, reference_bytes = load_canonical_reference(
        reference_root
    )
    reference_mesh = load_mesh_bytes(
        reference_bytes,
        suffix=Path(manifest["reference_ply"]["path"]).suffix,
        label="reference",
    )
    candidate_bytes = read_artifact_bytes(candidate_path)
    candidate = load_mesh_bytes(
        candidate_bytes, suffix=candidate_path.suffix, label="candidate"
    )
    frame = CanonicalFrame((0.0, 0.0, 0.0), 1.0)
    _assert_canonical_bounds(reference_mesh, "reference")

    reference_tree = voxelize_mesh(reference_mesh, frame, MAX_DEPTH, backend=backend)
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    exterior = measure_exterior_surface(
        np.asarray(candidate.triangles, dtype=np.float64)
    )
    validate_exterior_measurement(exterior)
    if candidate_digest == manifest["reference_ply"]["sha256"]:
        candidate_tree = reference_tree
    elif len(exterior.interior_triangles):
        candidate_tree = build_lattice_tree(
            exterior.interior_triangles,
            MAX_DEPTH,
            backend=backend,
        )
    else:
        candidate_tree = SurfaceTree.empty(MAX_DEPTH)

    reference_sets = _occupancy_by_depth(reference_tree)
    candidate_sets = _occupancy_by_depth(candidate_tree)
    errors_by_depth = _errors_by_depth(reference_sets, candidate_sets)
    missing_tree = tree_from_codes(reference_sets[-1] - candidate_sets[-1], MAX_DEPTH)
    excess_tree = tree_from_codes(candidate_sets[-1] - reference_sets[-1], MAX_DEPTH)
    step_root = f"{output_root.name}/steps/{step:06d}"
    repair_targets = partition_repair_targets(
        missing_tree,
        excess_tree,
        source_step=step,
        step_root=step_root,
        exterior=exterior,
    )
    observable_digest = _observable_sha256(candidate_tree.logical_sha256, exterior)

    session = _session_document(
        reference_root,
        output_root,
        manifest,
        normalization,
        reference_tree,
    )
    parent_measurement = None
    if output_root.exists():
        _validate_existing_session(output_root, session, reference_tree)
        if compare_to is not None:
            parent_measurement = _load_parent_measurement(output_root, compare_to)
    elif step != 0:
        raise OctreeError("nonzero step requires an existing VoxBlame session")
    measurement = _measurement_document(
        output_root,
        step=step,
        compare_to=compare_to,
        session=session,
        candidate_mesh_sha256=candidate_digest,
        candidate_tree=candidate_tree,
        errors_by_depth=errors_by_depth,
        missing_tree=missing_tree,
        excess_tree=excess_tree,
        repair_targets=repair_targets.report,
        exterior=exterior,
        observable_digest=observable_digest,
        no_observable_geometry_change=(
            parent_measurement is not None
            and parent_measurement["measurement"]["observable_sha256"]
            == observable_digest
        ),
    )
    summary = _summary_document(measurement)
    validate_session_contract(session)
    _validate_measurement_slice(session, measurement, summary)
    artifacts = _StepArtifacts(
        candidate_tree=candidate_tree,
        missing_tree=missing_tree,
        excess_tree=excess_tree,
        exterior_bytes=exterior.snapshot_bytes,
        target_mask_bytes=repair_targets.mask_bytes,
        measurement=measurement,
        summary=summary,
    )
    if output_root.exists():
        idempotent = _publish_step(
            output_root,
            step=step,
            artifacts=artifacts,
        )
    else:
        _publish_initial(
            output_root,
            session=session,
            reference_tree=reference_tree,
            artifacts=artifacts,
        )
        idempotent = False
    return MeasureStepResult(
        summary=summary,
        idempotent=idempotent,
        backend=backend_identity(backend),
    )


def _validate_ancestry(step: int, compare_to: int | None) -> None:
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise OctreeError("step must be a non-negative integer")
    if step == 0:
        if compare_to is not None:
            raise OctreeError("step 0 compare_to must be null")
        return
    if (
        not isinstance(compare_to, int)
        or isinstance(compare_to, bool)
        or compare_to < 0
        or compare_to >= step
    ):
        raise OctreeError("nonzero step requires an explicit earlier compare_to")


def _validate_existing_session(
    output: Path,
    expected_session: dict[str, Any],
    expected_reference_tree: SurfaceTree,
) -> None:
    try:
        session = json.loads((output / "session.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OctreeError("existing VoxBlame session is incomplete or invalid") from exc
    if session != expected_session:
        raise OctreeError("canonical reference does not match the existing session")
    reference_tree = read_surface_tree(output / "reference.vbsvo")
    if reference_tree.logical_sha256 != expected_reference_tree.logical_sha256:
        raise OctreeError("existing reference tree identity mismatch")


def _load_parent_measurement(output: Path, step: int) -> dict[str, Any]:
    try:
        measurement = json.loads(
            (output / "steps" / f"{step:06d}" / "measurement.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise OctreeError(f"compare_to step {step} is not published") from exc
    if (
        measurement.get("schema") != MEASUREMENT_SCHEMA
        or measurement.get("step") != step
    ):
        raise OctreeError(f"compare_to step {step} is invalid")
    return measurement


def _assert_canonical_bounds(mesh: trimesh.Trimesh, label: str) -> None:
    vertices = mesh_vertices(mesh, label)
    lower = -0.5 - BOUNDARY_EPSILON
    upper = 0.5 + BOUNDARY_EPSILON
    if np.any(vertices < lower) or np.any(vertices > upper):
        raise OctreeError(f"{label} mesh is outside the canonical cube")


def _occupancy_by_depth(tree: SurfaceTree) -> list[set[int]]:
    leaves = set(int(code) for code in tree.iter_leaf_codes())
    return [
        {code >> (3 * (MAX_DEPTH - depth)) for code in leaves}
        for depth in range(1, MAX_DEPTH + 1)
    ]


def _errors_by_depth(
    reference_sets: list[set[int]], candidate_sets: list[set[int]]
) -> list[dict[str, Any]]:
    result = []
    for depth, (reference, candidate) in enumerate(
        zip(reference_sets, candidate_sets, strict=True), start=1
    ):
        missing = reference - candidate
        excess = candidate - reference
        union = reference | candidate
        error_count = len(missing) + len(excess)
        result.append(
            {
                "depth": depth,
                "reference_surface_count": len(reference),
                "candidate_surface_count": len(candidate),
                "missing_surface_count": len(missing),
                "excess_surface_count": len(excess),
                "union_surface_count": len(union),
                "surface_error_count": error_count,
                "surface_error_rate": error_count / len(union) if union else 0.0,
            }
        )
    return result


def _session_document(
    reference_root: Path,
    output_root: Path,
    manifest: dict[str, Any],
    normalization: dict[str, Any],
    reference_tree: SurfaceTree,
) -> dict[str, Any]:
    return {
        "schema": "voxblame.session/2",
        "coordinate_contract": COORDINATE_CONTRACT,
        "semantic_units": None,
        "max_depth": MAX_DEPTH,
        "boundary_epsilon": BOUNDARY_EPSILON,
        "canonical_reference": {
            "canonical_reference_sha256": manifest["canonical_reference_sha256"],
            "reference_ply_path": (
                f"{reference_root.name}/{manifest['reference_ply']['path']}"
            ),
            "reference_ply_sha256": manifest["reference_ply"]["sha256"],
            "triangle_set_sha256": normalization["triangle_set_sha256"],
            "normalization_json_path": (
                f"{reference_root.name}/{manifest['normalization_json']['path']}"
            ),
            "normalization_json_sha256": manifest["normalization_json"]["sha256"],
            "interior_tree_path": f"{output_root.name}/reference.vbsvo",
            "interior_tree_sha256": reference_tree.logical_sha256,
        },
        "profiles": {
            "surface_occupancy": _SURFACE_PROFILE,
            "target_partition": _TARGET_PROFILE,
            "exterior_surface": _EXTERIOR_PROFILE,
        },
    }


def _measurement_document(
    output_root: Path,
    *,
    step: int,
    compare_to: int | None,
    session: dict[str, Any],
    candidate_mesh_sha256: str,
    candidate_tree: SurfaceTree,
    errors_by_depth: list[dict[str, Any]],
    missing_tree: SurfaceTree,
    excess_tree: SurfaceTree,
    repair_targets: dict[str, Any],
    exterior: ExteriorMeasurement,
    observable_digest: str,
    no_observable_geometry_change: bool,
) -> dict[str, Any]:
    step_root = f"{output_root.name}/steps/{step:06d}"
    reference = session["canonical_reference"]
    exact_exterior = exterior.exact
    resolution = exterior.resolution
    exterior_document = {
        "storage_schema": EXTERIOR_SNAPSHOT_SCHEMA,
        "path": f"{step_root}/exterior.json",
        "logical_sha256": exterior.logical_sha256,
        "surface_present": exact_exterior["surface_present"],
        "surface_cell_count": exterior.surface_cell_count,
        "bounds_canonical": exact_exterior["bounds_canonical"],
        "centroid_canonical": exact_exterior["centroid_canonical"],
        "nearest_overrun": exact_exterior["nearest_overrun"],
        "farthest_overrun": exact_exterior["farthest_overrun"],
        "outside_directions": exact_exterior["outside_directions"],
        "diagnostic_grid_depth": resolution["diagnostic_grid_depth"],
        "coarsened": resolution["coarsened"],
    }
    return {
        "schema": MEASUREMENT_SCHEMA,
        "coordinate_contract": COORDINATE_CONTRACT,
        "max_depth": MAX_DEPTH,
        "step": step,
        "compare_to": compare_to,
        "canonical_reference": {
            "canonical_reference_sha256": reference["canonical_reference_sha256"],
            "reference_ply_sha256": reference["reference_ply_sha256"],
            "triangle_set_sha256": reference["triangle_set_sha256"],
            "interior_tree_sha256": reference["interior_tree_sha256"],
        },
        "measurement": {
            "candidate_mesh_sha256": candidate_mesh_sha256,
            "interior_tree_sha256": candidate_tree.logical_sha256,
            "exterior_snapshot_sha256": exterior.logical_sha256,
            "observable_sha256": observable_digest,
        },
        "errors_by_depth": errors_by_depth,
        "depth_8_evidence": {
            "missing_surface": {
                "storage_schema": STORAGE_SCHEMA,
                "path": f"{step_root}/missing-depth8.vbsvo",
                "logical_sha256": missing_tree.logical_sha256,
                "surface_count": missing_tree.leaf_count,
            },
            "excess_surface": {
                "storage_schema": STORAGE_SCHEMA,
                "path": f"{step_root}/excess-depth8.vbsvo",
                "logical_sha256": excess_tree.logical_sha256,
                "surface_count": excess_tree.leaf_count,
            },
        },
        "repair_targets": repair_targets,
        "exterior_surface": exterior_document,
        "objective_facts": {
            "global_depth_8_zero": errors_by_depth[-1]["surface_error_count"] == 0,
            "out_of_frame_clear": not exact_exterior["surface_present"],
            "no_evidence_conflict": True,
        },
        "no_observable_geometry_change": no_observable_geometry_change,
    }


def _summary_document(measurement: dict[str, Any]) -> dict[str, Any]:
    artifact_path = (
        f"{Path(measurement['depth_8_evidence']['missing_surface']['path']).parent.as_posix()}"
        "/measurement.json"
    )
    return {
        "schema": MEASUREMENT_SUMMARY_SCHEMA,
        "coordinate_contract": measurement["coordinate_contract"],
        "max_depth": measurement["max_depth"],
        "step": measurement["step"],
        "compare_to": measurement["compare_to"],
        "report": artifact_path,
        "canonical_reference": measurement["canonical_reference"],
        "measurement": measurement["measurement"],
        "errors_by_depth": measurement["errors_by_depth"],
        "exterior_surface": measurement["exterior_surface"],
        "repair_targets": repair_target_page(measurement["repair_targets"]),
        "objective_facts": measurement["objective_facts"],
        "no_observable_geometry_change": measurement[
            "no_observable_geometry_change"
        ],
    }


def _validate_measurement_slice(
    session: dict[str, Any],
    measurement: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    if measurement["schema"] != MEASUREMENT_SCHEMA:
        raise OctreeError("measurement schema mismatch")
    if summary != _summary_document(measurement):
        raise OctreeError("measurement summary does not match its artifact")
    if measurement["canonical_reference"] != {
        key: session["canonical_reference"][key]
        for key in (
            "canonical_reference_sha256",
            "reference_ply_sha256",
            "triangle_set_sha256",
            "interior_tree_sha256",
        )
    }:
        raise OctreeError("measurement reference identity mismatch")
    if [item["depth"] for item in measurement["errors_by_depth"]] != list(
        range(1, MAX_DEPTH + 1)
    ):
        raise OctreeError("measurement depth evidence is not ordered 1 through 8")
    for item in measurement["errors_by_depth"]:
        missing = item["missing_surface_count"]
        excess = item["excess_surface_count"]
        union = item["union_surface_count"]
        error = item["surface_error_count"]
        if (
            union != item["reference_surface_count"] + excess
            or union != item["candidate_surface_count"] + missing
            or error != missing + excess
            or item["surface_error_rate"] != (error / union if union else 0.0)
        ):
            raise OctreeError("measurement depth evidence is inconsistent")
    depth_eight = measurement["errors_by_depth"][-1]
    evidence = measurement["depth_8_evidence"]
    if (
        evidence["missing_surface"]["surface_count"]
        != depth_eight["missing_surface_count"]
        or evidence["excess_surface"]["surface_count"]
        != depth_eight["excess_surface_count"]
    ):
        raise OctreeError("depth-8 set evidence count mismatch")
    targets = measurement["repair_targets"]
    if summary["repair_targets"] != repair_target_page(targets):
        raise OctreeError("measurement Repair Target page mismatch")
    if sum(
        target["error_profile"]["surface_error_count"]
        for target in targets["ordered_targets"]
        if target["kind"] == "interior"
    ) != depth_eight["surface_error_count"]:
        raise OctreeError("Repair Targets do not cover depth-8 error evidence")
    if sum(
        target["error_profile"]["surface_error_count"]
        for target in targets["ordered_targets"]
        if target["kind"] == "exterior"
    ) != measurement["exterior_surface"]["surface_cell_count"]:
        raise OctreeError("Repair Targets do not cover exterior error evidence")


def _observable_sha256(
    interior_sha256: str,
    exterior: ExteriorMeasurement,
) -> str:
    identity = {
        "schema": "voxblame.observable/1",
        "interior_tree_sha256": interior_sha256,
        "exterior_snapshot_sha256": exterior.logical_sha256,
        "exterior_profile": _EXTERIOR_PROFILE,
        "exterior_resolution": exterior.resolution,
    }
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _publish_initial(
    output: Path,
    *,
    session: dict[str, Any],
    reference_tree: SurfaceTree,
    artifacts: _StepArtifacts,
) -> None:
    if output.exists():
        raise OctreeError("VoxBlame output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".tmp-voxblame-{uuid.uuid4().hex}"
    try:
        stage.mkdir(parents=False, exist_ok=False)
        (stage / ".gitignore").write_text(".tmp-*\n", encoding="utf-8")
        _write_json(stage / "session.json", session)
        write_surface_tree(reference_tree, stage / "reference.vbsvo")
        step_root = stage / "steps/000000"
        _write_step(
            step_root,
            artifacts,
        )
        if (
            read_surface_tree(stage / "reference.vbsvo").logical_sha256
            != reference_tree.logical_sha256
        ):
            raise OctreeError("staged reference tree identity mismatch")
        stage.rename(output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise


def _publish_step(
    output: Path,
    *,
    step: int,
    artifacts: _StepArtifacts,
) -> bool:
    target = output / "steps" / f"{step:06d}"
    if target.exists():
        if _published_step_matches(
            target,
            artifacts,
        ):
            return True
        raise OctreeError(
            f"Measured Step {step} already exists with a different identity"
        )
    stage = output / f".tmp-{step:06d}-{uuid.uuid4().hex}"
    try:
        _write_step(
            stage,
            artifacts,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(target)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return False


def _published_step_matches(
    root: Path,
    artifacts: _StepArtifacts,
) -> bool:
    expected_names = {
        "candidate.vbsvo",
        "missing-depth8.vbsvo",
        "excess-depth8.vbsvo",
        "exterior.json",
        "measurement.json",
        "summary.json",
    }
    if artifacts.target_mask_bytes:
        expected_names.add("targets")
    try:
        if (
            not root.is_dir()
            or {path.name for path in root.iterdir()} != expected_names
        ):
            return False
        return (
            read_surface_tree(root / "candidate.vbsvo").logical_sha256
            == artifacts.candidate_tree.logical_sha256
            and read_surface_tree(root / "missing-depth8.vbsvo").logical_sha256
            == artifacts.missing_tree.logical_sha256
            and read_surface_tree(root / "excess-depth8.vbsvo").logical_sha256
            == artifacts.excess_tree.logical_sha256
            and (root / "exterior.json").read_bytes() == artifacts.exterior_bytes
            and _published_target_masks_match(root, artifacts.target_mask_bytes)
            and json.loads(
                (root / "measurement.json").read_text(encoding="utf-8")
            )
            == artifacts.measurement
            and json.loads((root / "summary.json").read_text(encoding="utf-8"))
            == artifacts.summary
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _write_step(root: Path, artifacts: _StepArtifacts) -> None:
    root.mkdir(parents=True, exist_ok=False)
    write_surface_tree(artifacts.candidate_tree, root / "candidate.vbsvo")
    write_surface_tree(artifacts.missing_tree, root / "missing-depth8.vbsvo")
    write_surface_tree(artifacts.excess_tree, root / "excess-depth8.vbsvo")
    (root / "exterior.json").write_bytes(artifacts.exterior_bytes)
    if artifacts.target_mask_bytes:
        target_root = root / "targets"
        target_root.mkdir()
        for name, data in sorted(artifacts.target_mask_bytes.items()):
            (target_root / name).write_bytes(data)
    _write_json(root / "measurement.json", artifacts.measurement)
    _write_json(root / "summary.json", artifacts.summary)
    if (
        read_surface_tree(root / "candidate.vbsvo").logical_sha256
        != artifacts.candidate_tree.logical_sha256
    ):
        raise OctreeError("staged candidate tree identity mismatch")
    if (
        read_surface_tree(root / "missing-depth8.vbsvo").logical_sha256
        != artifacts.missing_tree.logical_sha256
    ):
        raise OctreeError("staged missing-surface identity mismatch")
    if (
        read_surface_tree(root / "excess-depth8.vbsvo").logical_sha256
        != artifacts.excess_tree.logical_sha256
    ):
        raise OctreeError("staged excess-surface identity mismatch")


def _published_target_masks_match(
    root: Path, expected: dict[str, bytes]
) -> bool:
    target_root = root / "targets"
    if not expected:
        return not target_root.exists()
    if not target_root.is_dir():
        return False
    files = {path.name: path for path in target_root.iterdir()}
    return set(files) == set(expected) and all(
        files[name].read_bytes() == data for name, data in expected.items()
    )


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.write_bytes(_json_bytes(value))
    except OSError as exc:
        raise OctreeError(f"cannot write JSON artifact: {path}") from exc


__all__ = [
    "MEASUREMENT_SCHEMA",
    "MEASUREMENT_SUMMARY_SCHEMA",
    "MeasureStepResult",
    "measure_step",
]
