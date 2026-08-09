"""Prepare and atomically publish one immutable Canonical Reference."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
from typing import Any
from urllib.parse import unquote, urlparse
import uuid

import numpy as np
import trimesh

from meshscope.io import SUPPORTED_EXTENSIONS
from meshscope.voxblame.contracts import BOUNDARY_EPSILON, COORDINATE_CONTRACT


CANONICAL_REFERENCE_SCHEMA = "voxblame.canonical-reference/1"
NORMALIZATION_SCHEMA = "voxblame.normalization/1"
PREPARE_FAILURE_SCHEMA = "voxblame.prepare-reference-failure/1"
ZERO_EXTENT_EPSILON = 1e-15
_MAX_FAILURE_DETAIL = 2000
_MAX_PARTIAL_ARTIFACTS = 16
_NETWORK_SCHEMES = frozenset({"http", "https", "ftp", "s3", "gs"})


class PrepareReferenceError(ValueError):
    """Stable public failure raised while preparing a Canonical Reference."""

    def __init__(self, classification: str, detail: str, *, phase: str):
        self.classification = classification
        self.detail = _bounded(detail)
        self.phase = phase
        self.partial_artifacts: list[dict[str, Any]] = []
        super().__init__(self.detail)


@dataclass(frozen=True)
class PrepareReferenceResult:
    """Published manifest plus whether an existing valid publication was reused."""

    manifest: dict[str, Any]
    idempotent: bool


def prepare_reference(source: str | Path, output: str | Path) -> PrepareReferenceResult:
    """Capture, normalize, validate, and atomically publish ``source``.

    The result contains the exact published ``input.json`` document and an
    idempotency flag. Preparation happens entirely below a sibling temporary
    directory; ``output`` appears only after all artifacts have been reloaded
    and cross-checked.
    """

    source_path = Path(source).expanduser()
    output_path = Path(output).expanduser()
    stage: Path | None = None
    phase = "capture_input"
    try:
        source_path = _validate_source(source_path)
        dependencies = _geometry_dependencies(source_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        stage = output_path.parent / f".tmp-prepare-reference-{uuid.uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)

        captured_entry, captured_files = _capture_input(
            source_path, dependencies, stage
        )
        phase = "evaluate_scene"
        raw_triangles = _evaluate_scene(captured_entry)
        input_triangle_count = len(raw_triangles)
        if input_triangle_count == 0:
            raise PrepareReferenceError(
                "empty_geometry",
                "the evaluated scene contains no triangle faces",
                phase=phase,
            )
        if not np.all(np.isfinite(raw_triangles)):
            raise PrepareReferenceError(
                "non_finite_geometry",
                "the evaluated scene contains non-finite vertex coordinates",
                phase=phase,
            )

        cross = np.cross(
            raw_triangles[:, 1] - raw_triangles[:, 0],
            raw_triangles[:, 2] - raw_triangles[:, 0],
        )
        zero_area = np.all(cross == 0.0, axis=1)
        removed_zero_area_triangle_count = int(np.count_nonzero(zero_area))
        raw_triangles = np.ascontiguousarray(raw_triangles[~zero_area], dtype=np.float64)
        if len(raw_triangles) == 0:
            raise PrepareReferenceError(
                "all_degenerate_geometry",
                "all evaluated triangles have strictly zero area",
                phase=phase,
            )

        phase = "normalize_geometry"
        raw_lower = raw_triangles.reshape((-1, 3)).min(axis=0)
        raw_upper = raw_triangles.reshape((-1, 3)).max(axis=0)
        extents = raw_upper - raw_lower
        max_extent = float(np.max(extents))
        if not math.isfinite(max_extent) or max_extent <= ZERO_EXTENT_EPSILON:
            raise PrepareReferenceError(
                "zero_extent_geometry",
                f"evaluated maximum extent must exceed {ZERO_EXTENT_EPSILON:g}",
                phase=phase,
            )
        center = (raw_lower + raw_upper) / 2.0
        scale = 1.0 / max_extent
        canonical_triangles = np.ascontiguousarray(
            (raw_triangles - center) * scale, dtype=np.float64
        )
        canonical_triangles[canonical_triangles == 0.0] = 0.0
        canonical_lower = canonical_triangles.reshape((-1, 3)).min(axis=0)
        canonical_upper = canonical_triangles.reshape((-1, 3)).max(axis=0)

        phase = "stage_artifacts"
        reference_path = stage / "reference.ply"
        _write_binary_float64_ply(reference_path, canonical_triangles)
        reference_sha256 = _sha256_file(reference_path)
        raw_to_canonical, canonical_to_raw = _affine_matrices(center, scale)
        entry_record = next(item for item in captured_files if item["role"] == "entry")
        dependency_records = [
            item for item in captured_files if item["role"] == "geometry_dependency"
        ]
        normalization = {
            "schema": NORMALIZATION_SCHEMA,
            "coordinate_contract": COORDINATE_CONTRACT,
            "semantic_units": None,
            "boundary_epsilon": BOUNDARY_EPSILON,
            "method": {
                "name": "trellis2_max_extent",
                "version": 1,
                "pca_rotation": False,
                "per_axis_scaling": False,
                "padding": False,
                "clamp": False,
                "unit_inference": False,
                "geometry_repair": False,
            },
            "importer": {
                "name": "trimesh.load_scene",
                "version": trimesh.__version__,
                "entry_format": source_path.suffix.lower(),
            },
            "evaluated_scene_policy": {
                "transforms": "evaluate_all_scene_nodes",
                "instances": "materialize_each_scene_instance",
                "triangulation": "deterministic_importer_triangles",
                "axis_policy": "use_evaluated_vertex_coordinates",
                "additional_axis_conversion": False,
                "removed_surface": "strictly_zero_area_triangles_only",
            },
            "raw_entry": entry_record,
            "local_geometry_dependencies": dependency_records,
            "evaluated_raw_triangle_sha256": _triangle_set_sha256(raw_triangles),
            "raw_bounds": _bounds(raw_lower, raw_upper),
            "center": _vector(center),
            "scale": scale,
            "raw_to_canonical": _matrix(raw_to_canonical),
            "canonical_to_raw": _matrix(canonical_to_raw),
            "input_triangle_count": input_triangle_count,
            "removed_zero_area_triangle_count": removed_zero_area_triangle_count,
            "canonical_triangle_count": len(canonical_triangles),
            "canonical_bounds": _bounds(canonical_lower, canonical_upper),
            "reference_ply": {
                "path": "reference.ply",
                "sha256": reference_sha256,
                "size_bytes": reference_path.stat().st_size,
                "format": "binary_little_endian",
                "vertex_dtype": "float64",
                "face_index_dtype": "int32",
            },
            "triangle_set_sha256": _triangle_set_sha256(canonical_triangles),
        }
        normalization_path = stage / "normalization.json"
        _write_json(normalization_path, normalization)
        normalization_sha256 = _sha256_file(normalization_path)
        canonical_reference_sha256 = hashlib.sha256(
            b"voxblame.canonical-reference/1\0"
            + bytes.fromhex(normalization_sha256)
        ).hexdigest()
        manifest = {
            "schema": CANONICAL_REFERENCE_SCHEMA,
            "coordinate_contract": COORDINATE_CONTRACT,
            "semantic_units": None,
            "boundary_epsilon": BOUNDARY_EPSILON,
            "canonical_reference_sha256": canonical_reference_sha256,
            "captured_files": captured_files,
            "reference_ply": normalization["reference_ply"],
            "normalization_json": {
                "path": "normalization.json",
                "sha256": normalization_sha256,
                "size_bytes": normalization_path.stat().st_size,
            },
            "triangle_set_sha256": normalization["triangle_set_sha256"],
            "input_triangle_count": input_triangle_count,
            "removed_zero_area_triangle_count": removed_zero_area_triangle_count,
            "canonical_triangle_count": len(canonical_triangles),
        }
        _write_json(stage / "input.json", manifest)

        phase = "reload_and_validate"
        _validate_stage(stage, manifest, normalization)
        phase = "publish"
        published, idempotent = _publish_stage(stage, output_path, manifest)
        stage = None
        failure_path = _failure_path(output_path)
        if failure_path.exists():
            try:
                failure_path.unlink()
            except OSError:
                pass
        return PrepareReferenceResult(manifest=published, idempotent=idempotent)
    except PrepareReferenceError as error:
        error.partial_artifacts = _partial_artifacts(stage)
        raise
    except Exception as exc:
        error = PrepareReferenceError(
            "prepare_reference_failed",
            f"{type(exc).__name__}: {exc}",
            phase=phase,
        )
        error.partial_artifacts = _partial_artifacts(stage)
        raise error from exc
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def publish_prepare_failure(
    *,
    source: str | Path,
    output: str | Path,
    error: PrepareReferenceError,
    partial_root: Path | None = None,
) -> Path:
    """Atomically publish compact setup-failure evidence outside ``output``."""

    source_path = Path(source).expanduser()
    output_path = Path(output).expanduser()
    evidence_path = _failure_path(output_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    source_record: dict[str, Any] = {"path": str(source_path)}
    if source_path.is_file():
        try:
            source_record.update(
                sha256=_sha256_file(source_path), size_bytes=source_path.stat().st_size
            )
        except OSError:
            pass
    evidence = {
        "schema": PREPARE_FAILURE_SCHEMA,
        "classification": error.classification,
        "phase": error.phase,
        "detail": _bounded(error.detail),
        "command": {
            "name": "mesh-compare voxblame-prepare-reference",
            "arguments": [str(source_path), "--output", str(output_path)],
        },
        "input": source_record,
        "intended_output": str(output_path),
        "partial_artifacts": (
            _partial_artifacts(partial_root)
            if partial_root is not None
            else error.partial_artifacts[:_MAX_PARTIAL_ARTIFACTS]
        ),
    }
    temporary = evidence_path.parent / f".{evidence_path.name}.tmp-{uuid.uuid4().hex}"
    _write_json(temporary, evidence)
    os.replace(temporary, evidence_path)
    return evidence_path


def _validate_source(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PrepareReferenceError(
            "unreadable_input", f"cannot resolve input: {path}", phase="capture_input"
        ) from exc
    if not resolved.is_file():
        raise PrepareReferenceError(
            "unreadable_input", f"input is not a regular file: {path}", phase="capture_input"
        )
    if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise PrepareReferenceError(
            "unreadable_input",
            f"unsupported input format: {resolved.suffix.lower()}",
            phase="capture_input",
        )
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise PrepareReferenceError(
            "unreadable_input", f"cannot read input: {path}", phase="capture_input"
        ) from exc
    return resolved


def _geometry_dependencies(source: Path) -> list[tuple[Path, PurePosixPath]]:
    if source.suffix.lower() != ".gltf":
        return []
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrepareReferenceError(
            "unreadable_input", "cannot parse glTF JSON", phase="capture_input"
        ) from exc
    buffers = document.get("buffers", [])
    if not isinstance(buffers, list):
        raise PrepareReferenceError(
            "unevaluable_scene", "glTF buffers must be an array", phase="capture_input"
        )
    result: dict[str, tuple[Path, PurePosixPath]] = {}
    root = source.parent.resolve()
    for buffer in buffers:
        if not isinstance(buffer, dict):
            raise PrepareReferenceError(
                "unevaluable_scene", "glTF buffer entry must be an object", phase="capture_input"
            )
        uri = buffer.get("uri")
        if uri is None or (isinstance(uri, str) and uri.startswith("data:")):
            continue
        if not isinstance(uri, str) or not uri:
            raise PrepareReferenceError(
                "unresolved_local_dependency",
                "glTF geometry buffer URI must be a non-empty string",
                phase="capture_input",
            )
        parsed = urlparse(uri)
        if parsed.scheme.lower() in _NETWORK_SCHEMES or parsed.netloc:
            raise PrepareReferenceError(
                "network_dependency",
                "network geometry dependencies are not allowed",
                phase="capture_input",
            )
        if parsed.scheme or parsed.query or parsed.fragment or re.match(r"^[A-Za-z]:[\\/]", uri):
            raise PrepareReferenceError(
                "unresolved_local_dependency",
                "geometry dependencies must be relative local paths",
                phase="capture_input",
            )
        relative = PurePosixPath(unquote(parsed.path))
        if relative.is_absolute() or ".." in relative.parts:
            raise PrepareReferenceError(
                "unresolved_local_dependency",
                "geometry dependency paths cannot contain traversal segments",
                phase="capture_input",
            )
        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PrepareReferenceError(
                "unresolved_local_dependency",
                "geometry dependency escapes the input directory",
                phase="capture_input",
            ) from exc
        if not candidate.is_file():
            raise PrepareReferenceError(
                "unresolved_local_dependency",
                f"geometry dependency is unavailable: {relative.as_posix()}",
                phase="capture_input",
            )
        result[relative.as_posix()] = (candidate, relative)
    return [result[key] for key in sorted(result)]


def _capture_input(
    source: Path,
    dependencies: list[tuple[Path, PurePosixPath]],
    stage: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    original = stage / "original"
    original.mkdir()
    records: list[dict[str, Any]] = []

    def capture(path: Path, relative: PurePosixPath, role: str) -> Path:
        destination = original.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        record = {
            "role": role,
            "source_path": relative.as_posix(),
            "captured_path": PurePosixPath("original", *relative.parts).as_posix(),
            "sha256": _sha256_file(destination),
            "size_bytes": destination.stat().st_size,
        }
        records.append(record)
        return destination

    captured_entry = capture(source, PurePosixPath(source.name), "entry")
    for dependency, relative in dependencies:
        capture(dependency, relative, "geometry_dependency")
    return captured_entry, records


def _evaluate_scene(path: Path) -> np.ndarray:
    try:
        scene = trimesh.load_scene(path, process=False, allow_remote=False)
    except Exception as exc:
        raise PrepareReferenceError(
            "unevaluable_scene",
            f"scene importer could not evaluate the input: {type(exc).__name__}",
            phase="evaluate_scene",
        ) from exc
    if not isinstance(scene, trimesh.Scene):
        raise PrepareReferenceError(
            "unevaluable_scene",
            "scene importer returned an unsupported value",
            phase="evaluate_scene",
        )
    triangles: list[np.ndarray] = []
    nodes = sorted(scene.graph.nodes_geometry, key=lambda value: str(value))
    for node in nodes:
        try:
            transform, geometry_name = scene.graph.get(node)
            geometry = scene.geometry[geometry_name]
        except Exception as exc:
            raise PrepareReferenceError(
                "unevaluable_scene",
                f"cannot evaluate scene node: {node}",
                phase="evaluate_scene",
            ) from exc
        matrix = np.asarray(transform, dtype=np.float64)
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            raise PrepareReferenceError(
                "unevaluable_scene",
                f"scene node has an invalid transform: {node}",
                phase="evaluate_scene",
            )
        if not isinstance(geometry, trimesh.Trimesh):
            raise PrepareReferenceError(
                "unevaluable_scene",
                f"scene node is not triangle geometry: {node}",
                phase="evaluate_scene",
            )
        vertices = np.asarray(geometry.vertices, dtype=np.float64)
        faces = np.asarray(geometry.faces)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,):
            raise PrepareReferenceError(
                "unevaluable_scene",
                "mesh vertices are not three-dimensional",
                phase="evaluate_scene",
            )
        if faces.ndim != 2 or (len(faces) and faces.shape[1:] != (3,)):
            raise PrepareReferenceError(
                "unevaluable_scene", "mesh faces could not be triangulated", phase="evaluate_scene"
            )
        if not len(faces):
            continue
        if not np.all(np.isfinite(vertices)):
            raise PrepareReferenceError(
                "non_finite_geometry", "scene contains non-finite vertices", phase="evaluate_scene"
            )
        try:
            evaluated = trimesh.transform_points(vertices, matrix)
            node_triangles = np.asarray(evaluated[faces], dtype=np.float64)
        except Exception as exc:
            raise PrepareReferenceError(
                "unevaluable_scene",
                f"cannot materialize scene node: {node}",
                phase="evaluate_scene",
            ) from exc
        if not np.all(np.isfinite(node_triangles)):
            raise PrepareReferenceError(
                "non_finite_geometry",
                "evaluated scene contains non-finite coordinates",
                phase="evaluate_scene",
            )
        triangles.append(node_triangles)
    if not triangles:
        return np.empty((0, 3, 3), dtype=np.float64)
    return np.ascontiguousarray(np.concatenate(triangles, axis=0), dtype=np.float64)


def _write_binary_float64_ply(path: Path, triangles: np.ndarray) -> None:
    ordered = sorted((_oriented_triangle_key(triangle) for triangle in triangles))
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment VoxBlame Canonical Reference\n"
        f"element vertex {len(ordered) * 3}\n"
        "property double x\n"
        "property double y\n"
        "property double z\n"
        f"element face {len(ordered)}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")
    with path.open("wb") as stream:
        stream.write(header)
        for triangle in ordered:
            for vertex in triangle:
                stream.write(struct.pack("<ddd", *vertex))
        for index in range(len(ordered)):
            base = index * 3
            stream.write(struct.pack("<Biii", 3, base, base + 1, base + 2))


def _validate_stage(
    stage: Path, manifest: dict[str, Any], normalization: dict[str, Any]
) -> None:
    reference_path = stage / "reference.ply"
    try:
        reloaded = trimesh.load(reference_path, force="mesh", process=False)
    except Exception as exc:
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "staged Canonical Reference PLY could not be reloaded",
            phase="reload_and_validate",
        ) from exc
    if not isinstance(reloaded, trimesh.Trimesh):
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "staged PLY did not reload as a triangle mesh",
            phase="reload_and_validate",
        )
    vertices = np.asarray(reloaded.vertices, dtype=np.float64)
    faces = np.asarray(reloaded.faces)
    if len(faces) != normalization["canonical_triangle_count"]:
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "staged PLY triangle count changed on reload",
            phase="reload_and_validate",
        )
    if not np.all(np.isfinite(vertices)):
        raise PrepareReferenceError(
            "non_finite_geometry",
            "staged PLY contains non-finite coordinates",
            phase="reload_and_validate",
        )
    lower = -0.5 - BOUNDARY_EPSILON
    upper = 0.5 + BOUNDARY_EPSILON
    if np.any(vertices < lower) or np.any(vertices > upper):
        raise PrepareReferenceError(
            "canonical_bounds_violation",
            "staged PLY exceeds the canonical boundary epsilon",
            phase="reload_and_validate",
        )
    reloaded_triangles = np.asarray(vertices[faces], dtype=np.float64)
    if _triangle_set_sha256(reloaded_triangles) != manifest["triangle_set_sha256"]:
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "staged PLY triangle-set identity changed on reload",
            phase="reload_and_validate",
        )
    if _sha256_file(reference_path) != manifest["reference_ply"]["sha256"]:
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "staged PLY byte identity changed",
            phase="reload_and_validate",
        )
    normalization_path = stage / manifest["normalization_json"]["path"]
    if _sha256_file(normalization_path) != manifest["normalization_json"]["sha256"]:
        raise PrepareReferenceError(
            "canonical_reload_failed",
            "normalization identity changed",
            phase="reload_and_validate",
        )


def _publish_stage(
    stage: Path, output: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    if output.exists():
        try:
            if not output.is_dir() or output.is_symlink():
                raise OSError("output is not an ordinary directory")
            existing = json.loads((output / "input.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrepareReferenceError(
                "conflicting_publication",
                "output already exists without a readable Canonical Reference manifest",
                phase="publish",
            ) from exc
        if existing != manifest:
            raise PrepareReferenceError(
                "conflicting_publication",
                "output already contains a different Canonical Reference",
                phase="publish",
            )
        try:
            normalization = json.loads(
                (output / existing["normalization_json"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            for record in existing["captured_files"]:
                captured = output.joinpath(*PurePosixPath(record["captured_path"]).parts)
                if (
                    not captured.is_file()
                    or captured.stat().st_size != record["size_bytes"]
                    or _sha256_file(captured) != record["sha256"]
                ):
                    raise OSError("captured input identity mismatch")
            _validate_stage(output, existing, normalization)
        except (KeyError, TypeError, OSError, json.JSONDecodeError, PrepareReferenceError) as exc:
            raise PrepareReferenceError(
                "conflicting_publication",
                "existing Canonical Reference publication is incomplete or corrupt",
                phase="publish",
            ) from exc
        shutil.rmtree(stage)
        return existing, True
    try:
        stage.rename(output)
    except FileExistsError as exc:
        raise PrepareReferenceError(
            "conflicting_publication", "output appeared concurrently", phase="publish"
        ) from exc
    return manifest, False


def _triangle_set_sha256(triangles: np.ndarray) -> str:
    records = []
    for triangle in triangles:
        vertices = sorted(_vertex_bytes(vertex) for vertex in triangle)
        records.append(b"".join(vertices))
    records.sort()
    digest = hashlib.sha256()
    digest.update(b"voxblame.triangle-set/1\0")
    digest.update(struct.pack("<Q", len(records)))
    for record in records:
        digest.update(record)
    return digest.hexdigest()


def _oriented_triangle_key(
    triangle: np.ndarray,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    vertices = tuple(tuple(_clean_float(value) for value in vertex) for vertex in triangle)
    rotations = (vertices, vertices[1:] + vertices[:1], vertices[2:] + vertices[:2])
    return min(rotations)


def _vertex_bytes(vertex: np.ndarray) -> bytes:
    return struct.pack("<ddd", *(_clean_float(value) for value in vertex))


def _affine_matrices(center: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
    raw_to_canonical = np.eye(4, dtype=np.float64)
    raw_to_canonical[:3, :3] *= scale
    raw_to_canonical[:3, 3] = -center * scale
    canonical_to_raw = np.eye(4, dtype=np.float64)
    canonical_to_raw[:3, :3] /= scale
    canonical_to_raw[:3, 3] = center
    return raw_to_canonical, canonical_to_raw


def _bounds(lower: np.ndarray, upper: np.ndarray) -> dict[str, list[float]]:
    return {"min": _vector(lower), "max": _vector(upper)}


def _vector(value: np.ndarray) -> list[float]:
    return [_clean_float(item) for item in value]


def _matrix(value: np.ndarray) -> list[list[float]]:
    return [_vector(row) for row in value]


def _clean_float(value: float) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )


def _failure_path(output: Path) -> Path:
    return output.with_suffix(".failure.json")


def _partial_artifacts(root: Path | None) -> list[dict[str, Any]]:
    if root is None or not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if len(records) >= _MAX_PARTIAL_ARTIFACTS:
            break
        try:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        except OSError:
            continue
    return records


def _bounded(detail: str) -> str:
    return str(detail)[:_MAX_FAILURE_DETAIL]


__all__ = [
    "CANONICAL_REFERENCE_SCHEMA",
    "NORMALIZATION_SCHEMA",
    "PREPARE_FAILURE_SCHEMA",
    "PrepareReferenceError",
    "PrepareReferenceResult",
    "prepare_reference",
    "publish_prepare_failure",
]
