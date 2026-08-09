"""Validated readers shared by canonical measurement and preview paths."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import trimesh

from meshscope.voxblame.contracts import BOUNDARY_EPSILON, COORDINATE_CONTRACT
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.frame import mesh_vertices
from meshscope.voxblame.prepare_reference import (
    CANONICAL_REFERENCE_SCHEMA,
    NORMALIZATION_SCHEMA,
)


def load_canonical_reference(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Read and cross-check one published Canonical Reference."""

    try:
        manifest = json.loads(read_artifact_bytes(root / "input.json"))
        normalization_path = root / manifest["normalization_json"]["path"]
        normalization_bytes = read_artifact_bytes(normalization_path)
        normalization = json.loads(normalization_bytes)
        reference_path = root / manifest["reference_ply"]["path"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError(
            "canonical reference publication is incomplete or invalid"
        ) from exc
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
    reference_bytes = read_artifact_bytes(reference_path)
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


def load_mesh_bytes(data: bytes, *, suffix: str, label: str) -> trimesh.Trimesh:
    """Load finite indexed triangle geometry without importer processing."""

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


def read_artifact_bytes(path: Path) -> bytes:
    """Read an artifact with the stable VoxBlame error boundary."""

    try:
        return path.read_bytes()
    except OSError as exc:
        raise OctreeError(f"cannot read artifact bytes: {path}") from exc
