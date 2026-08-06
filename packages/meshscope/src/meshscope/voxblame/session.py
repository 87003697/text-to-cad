"""Application orchestration for one immutable VoxBlame candidate step."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from meshscope.io import load
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.frame import CanonicalFrame, mesh_vertices
from meshscope.voxblame.grading import (
    compare_error_trees,
    grade_surface_trees,
    select_next_action,
)
from meshscope.voxblame.reporting import (
    build_report,
    summarize_report,
    tree_metadata,
)
from meshscope.voxblame.store import (
    SESSION_SCHEMA,
    VoxBlameStore,
    validate_session_metadata,
)
from meshscope.voxblame.tree import validate_depth
from meshscope.voxblame.voxelize import Backend, voxelize_mesh


MeshSource = trimesh.Trimesh | str | Path


def run_step(
    reference_mesh: MeshSource,
    candidate_mesh: MeshSource,
    state_dir: str | Path,
    step: int,
    max_depth: int = 8,
    compare_to: int | None = None,
    *,
    backend: Backend = "auto",
) -> dict[str, Any]:
    """Evaluate and atomically publish one immutable candidate snapshot."""
    validate_depth(max_depth)
    compare_to = _validate_step(step, compare_to)
    reference = load_mesh(reference_mesh, "reference")
    candidate = load_mesh(candidate_mesh, "candidate")
    frame = CanonicalFrame.from_reference(reference)
    frame.assert_fits(reference, "reference")
    reference_source_digest = mesh_source_digest(reference_mesh, reference)
    candidate_source_digest = mesh_source_digest(candidate_mesh, candidate)
    store = VoxBlameStore(state_dir)

    if store.session_path.exists():
        session = store.load_session()
        if session.get("schema") != SESSION_SCHEMA:
            raise OctreeError("unsupported or invalid VoxBlame session schema")
        reference_tree = store.load_reference_tree(max_depth)
        stored_frame = CanonicalFrame.from_json(session.get("frame", {}))
        if stored_frame != frame:
            raise OctreeError(
                "reference frame metadata does not match the existing session"
            )
        validate_session_metadata(
            session=session,
            frame_json=frame.to_json(),
            max_depth=max_depth,
            reference_source_digest=reference_source_digest,
            reference_tree=reference_tree,
        )
    else:
        if store.root.exists() and any(
            child.name != ".gitignore" and not child.name.startswith(".tmp-")
            for child in store.root.iterdir()
        ):
            raise OctreeError("state directory exists without a valid session")
        reference_tree = voxelize_mesh(
            reference, frame, max_depth, backend=backend
        )
        session = {
            "schema": SESSION_SCHEMA,
            "max_depth": max_depth,
            "frame": frame.to_json(),
            "reference": {
                "source_sha256": reference_source_digest,
                **tree_metadata(reference_tree),
            },
        }
        store.initialize(session, reference_tree)

    if candidate_source_digest == reference_source_digest:
        candidate_tree = reference_tree
    else:
        candidate_tree = voxelize_mesh(
            candidate, frame, max_depth, backend=backend
        )

    previous_tree = (
        store.load_candidate_tree(compare_to, max_depth)
        if compare_to is not None
        else None
    )
    previous_errors = (
        grade_surface_trees(reference_tree, previous_tree)
        if previous_tree is not None
        else []
    )
    current_errors = grade_surface_trees(reference_tree, candidate_tree)
    changes = (
        compare_error_trees(previous_errors, current_errors, max_depth)
        if previous_tree is not None
        else []
    )
    next_action = select_next_action(changes, current_errors, frame)
    report = build_report(
        step=step,
        compare_to=compare_to,
        max_depth=max_depth,
        frame=frame,
        reference_metadata=session["reference"],
        candidate_tree=candidate_tree,
        previous_tree=previous_tree,
        current_errors=current_errors,
        changes=changes,
        next_action=next_action,
    )
    published_report = store.publish_step(step, candidate_tree, report)
    return summarize_report(published_report, store.root)


def load_mesh(source: MeshSource, label: str) -> trimesh.Trimesh:
    if isinstance(source, trimesh.Trimesh):
        mesh = source.copy()
    else:
        try:
            mesh = load(source)
        except Exception as exc:
            raise OctreeError(str(exc)) from exc
    mesh_vertices(mesh, label)
    return mesh


def mesh_source_digest(source: MeshSource, mesh: trimesh.Trimesh) -> str:
    if not isinstance(source, trimesh.Trimesh):
        path = Path(source)
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise OctreeError(f"cannot read mesh bytes: {path}") from exc
    triangles = np.asarray(mesh.triangles, dtype="<f8")
    return hashlib.sha256(np.ascontiguousarray(triangles).tobytes()).hexdigest()


def _validate_step(step: int, compare_to: int | None) -> int | None:
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise OctreeError("step must be a non-negative integer")
    if step == 0 and compare_to is not None:
        raise OctreeError("step 0 compare_to must be null")
    if step > 0:
        if compare_to is None:
            compare_to = step - 1
        if (
            not isinstance(compare_to, int)
            or isinstance(compare_to, bool)
            or compare_to < 0
            or compare_to >= step
        ):
            raise OctreeError("compare_to must identify an earlier published step")
    return compare_to
