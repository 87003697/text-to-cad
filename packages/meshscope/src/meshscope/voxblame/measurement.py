"""Measure and atomically publish canonical VoxBlame candidate steps."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
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
from meshscope.voxblame.contracts import (
    BOUNDARY_EPSILON,
    COORDINATE_CONTRACT,
    MAX_DEPTH,
    validate_session_contract,
)
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.frame import CanonicalFrame, mesh_vertices
from meshscope.voxblame.prepare_reference import (
    CANONICAL_REFERENCE_SCHEMA,
    NORMALIZATION_SCHEMA,
)
from meshscope.voxblame.tree import SurfaceTree, tree_from_codes
from meshscope.voxblame.voxelize import Backend, voxelize_mesh


_EXTERIOR_SCHEMA = "voxblame.exterior-placeholder/1"
MEASUREMENT_SCHEMA = "voxblame.measurement/1"
MEASUREMENT_SUMMARY_SCHEMA = "voxblame.measurement-summary/1"
_SURFACE_PROFILE = "conservative_surface_occupancy/1"
_TARGET_PROFILE = "repair_target_partition/1"
_EXTERIOR_PROFILE = "signed_exterior_surface/1"


@dataclass(frozen=True)
class MeasureStepResult:
    """Published compact summary and whether an identical step was reused."""

    summary: dict[str, Any]
    idempotent: bool


@dataclass(frozen=True)
class _StepArtifacts:
    candidate_tree: SurfaceTree
    missing_tree: SurfaceTree
    excess_tree: SurfaceTree
    exterior_bytes: bytes
    measurement: dict[str, Any]
    summary: dict[str, Any]


def measure_step(
    canonical_reference: str | Path,
    candidate_mesh: str | Path,
    output: str | Path,
    *,
    step: int,
    compare_to: int | None = None,
    backend: Backend = "auto",
) -> MeasureStepResult:
    """Measure one in-frame candidate and publish a canonical Measured Step."""

    _validate_ancestry(step, compare_to)
    reference_root = Path(canonical_reference)
    candidate_path = Path(candidate_mesh)
    output_root = Path(output)
    manifest, normalization, reference_bytes = _load_canonical_reference(
        reference_root
    )
    reference_mesh = _load_mesh(
        reference_bytes,
        suffix=Path(manifest["reference_ply"]["path"]).suffix,
        label="reference",
    )
    candidate_bytes = _read_artifact_bytes(candidate_path)
    candidate = _load_mesh(
        candidate_bytes, suffix=candidate_path.suffix, label="candidate"
    )
    frame = CanonicalFrame((0.0, 0.0, 0.0), 1.0)
    _assert_canonical_bounds(reference_mesh, "reference")
    _assert_canonical_bounds(candidate, "candidate")

    reference_tree = voxelize_mesh(reference_mesh, frame, MAX_DEPTH, backend=backend)
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    if candidate_digest == manifest["reference_ply"]["sha256"]:
        candidate_tree = reference_tree
    else:
        candidate_tree = voxelize_mesh(candidate, frame, MAX_DEPTH, backend=backend)

    reference_sets = _occupancy_by_depth(reference_tree)
    candidate_sets = _occupancy_by_depth(candidate_tree)
    errors_by_depth = _errors_by_depth(reference_sets, candidate_sets)
    missing_tree = tree_from_codes(reference_sets[-1] - candidate_sets[-1], MAX_DEPTH)
    excess_tree = tree_from_codes(candidate_sets[-1] - reference_sets[-1], MAX_DEPTH)
    exterior_bytes, exterior_digest = _clear_exterior_snapshot()
    observable_digest = _observable_sha256(candidate_tree.logical_sha256, exterior_digest)

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
        exterior_digest=exterior_digest,
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
        exterior_bytes=exterior_bytes,
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
    return MeasureStepResult(summary=summary, idempotent=idempotent)


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


def _load_canonical_reference(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    try:
        manifest = json.loads(_read_artifact_bytes(root / "input.json"))
        normalization_path = root / manifest["normalization_json"]["path"]
        normalization_bytes = _read_artifact_bytes(normalization_path)
        normalization = json.loads(normalization_bytes)
        reference_path = root / manifest["reference_ply"]["path"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("canonical reference publication is incomplete or invalid") from exc
    if manifest.get("schema") != CANONICAL_REFERENCE_SCHEMA:
        raise OctreeError("canonical reference schema is unsupported")
    try:
        expected_reference_identity = hashlib.sha256(
            b"voxblame.canonical-reference/1\0"
            + bytes.fromhex(manifest["normalization_json"]["sha256"])
        ).hexdigest()
    except (KeyError, TypeError, ValueError) as exc:
        raise OctreeError("canonical reference identity mismatch") from exc
    if (
        normalization.get("schema") != NORMALIZATION_SCHEMA
        or manifest.get("coordinate_contract") != COORDINATE_CONTRACT
        or normalization.get("coordinate_contract") != COORDINATE_CONTRACT
        or manifest.get("semantic_units") is not None
        or normalization.get("semantic_units") is not None
        or manifest.get("boundary_epsilon") != BOUNDARY_EPSILON
        or normalization.get("boundary_epsilon") != BOUNDARY_EPSILON
        or manifest.get("reference_ply") != normalization.get("reference_ply")
        or manifest.get("triangle_set_sha256")
        != normalization.get("triangle_set_sha256")
        or manifest.get("canonical_reference_sha256") != expected_reference_identity
    ):
        raise OctreeError("canonical reference identity mismatch")
    reference_bytes = _read_artifact_bytes(reference_path)
    if (
        hashlib.sha256(reference_bytes).hexdigest()
        != manifest["reference_ply"]["sha256"]
    ):
        raise OctreeError("canonical reference PLY identity mismatch")
    if (
        hashlib.sha256(normalization_bytes).hexdigest()
        != manifest["normalization_json"]["sha256"]
    ):
        raise OctreeError("canonical reference normalization identity mismatch")
    return manifest, normalization, reference_bytes


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


def _load_mesh(data: bytes, *, suffix: str, label: str) -> trimesh.Trimesh:
    try:
        mesh = trimesh.load(
            io.BytesIO(data),
            file_type=suffix.lower().removeprefix("."),
            force="mesh",
            process=False,
        )
    except Exception as exc:
        raise OctreeError(f"cannot load {label} mesh bytes") from exc
    mesh_vertices(mesh, label)
    return mesh


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
    exterior_digest: str,
    observable_digest: str,
    no_observable_geometry_change: bool,
) -> dict[str, Any]:
    step_root = f"{output_root.name}/steps/{step:06d}"
    reference = session["canonical_reference"]
    exterior = {
        "storage_schema": _EXTERIOR_SCHEMA,
        "path": f"{step_root}/exterior.json",
        "logical_sha256": exterior_digest,
        "surface_present": False,
        "surface_cell_count": 0,
        "bounds_canonical": None,
        "centroid_canonical": None,
        "nearest_overrun": None,
        "farthest_overrun": None,
        "outside_directions": [],
        "diagnostic_grid_depth": MAX_DEPTH,
        "coarsened": False,
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
            "exterior_snapshot_sha256": exterior_digest,
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
        "exterior_surface": exterior,
        "objective_facts": {
            "global_depth_8_zero": errors_by_depth[-1]["surface_error_count"] == 0,
            "out_of_frame_clear": True,
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
        "artifact": artifact_path,
        "canonical_reference": measurement["canonical_reference"],
        "measurement": measurement["measurement"],
        "errors_by_depth": measurement["errors_by_depth"],
        "exterior_surface": measurement["exterior_surface"],
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


def _clear_exterior_snapshot() -> tuple[bytes, str]:
    value = {
        "schema": _EXTERIOR_SCHEMA,
        "coordinate_contract": COORDINATE_CONTRACT,
        "diagnostic_grid_depth": MAX_DEPTH,
        "surface_present": False,
    }
    data = _json_bytes(value)
    digest = hashlib.sha256(b"voxblame.exterior-placeholder/1\0" + data).hexdigest()
    return data, digest


def _observable_sha256(interior_sha256: str, exterior_sha256: str) -> str:
    identity = {
        "schema": "voxblame.observable/1",
        "interior_tree_sha256": interior_sha256,
        "exterior_snapshot_sha256": exterior_sha256,
        "exterior_profile": _EXTERIOR_PROFILE,
        "diagnostic_grid_depth": MAX_DEPTH,
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


def _read_artifact_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise OctreeError(f"cannot read artifact bytes: {path}") from exc


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
