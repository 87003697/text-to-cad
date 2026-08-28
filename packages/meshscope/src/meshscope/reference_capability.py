"""A small, bounded observation seam for one supervisor-owned reference.

The capability is deliberately constructed by the supervisor with the opaque
reference id and the material it owns.  The request handler accepts only that
id and the fixed summary observation; paths and mesh objects never cross the
handler interface.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, BinaryIO

import numpy as np
import trimesh

from meshscope.inspect import _compute_quality, _compute_stats
from meshscope.voxblame.contracts import BOUNDARY_EPSILON, COORDINATE_CONTRACT


REQUEST_SCHEMA = "meshscope.reference-request/1"
RESPONSE_SCHEMA = "meshscope.reference-response/1"
SUMMARY_SCHEMA = "meshscope.reference-summary/1"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
# The header preflight derives record-count bounds from these shortest valid
# ASCII records, then tightens them to the declared format and remaining file
# bytes before invoking trimesh.  There is no independent topology cap.
_MIN_ASCII_VERTEX_RECORD_BYTES = len(b"0 0 0\n")
_MIN_ASCII_FACE_RECORD_BYTES = len(b"3 0 0 0\n")
MAX_REFERENCE_ID_LENGTH = 128
PLY_HEADER_MAX_BYTES = 64 * 1024
PLY_HEADER_MAX_LINES = 1024
PLY_HEADER_MAX_LINE_BYTES = 4096
PCA_GAP_ABSOLUTE_EPSILON = 1e-8
PCA_GAP_RELATIVE_EPSILON = 1e-5
CANONICAL_MIN = -0.5
CANONICAL_MAX = 0.5

_REFERENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_PROHIBITED_METHODS = frozenset(
    {
        "components",
        "vertices",
        "faces",
        "triangles",
        "raw_bytes",
        "export",
        "to_trimesh",
        "sample_points",
        "raycast",
        "closest_point",
        "signed_distance",
        "occupancy",
        "fine_occupancy",
        "fine_occupancy_query",
        "query",
        "arbitrary_query",
        "roi",
        "arbitrary_roi",
        "slice",
        "slice_plane",
        "camera",
        "projection",
        "nearest_point",
    }
)


class ReferenceCapabilityError(ValueError):
    """Stable, path-free failure raised by the capability seam."""

    def __init__(self, classification: str):
        self.classification = classification
        self.detail = classification
        super().__init__(classification)


def _fail(classification: str) -> None:
    raise ReferenceCapabilityError(classification)


def _is_dict(value: object) -> bool:
    return type(value) is dict


def _number(value: Any) -> float:
    """Return one finite, platform-stable JSON number."""

    number = float(value)
    if not np.isfinite(number):
        _fail("invalid_reference_material")
    rounded = round(number, 8)
    return 0.0 if rounded == 0.0 else rounded


def _vector(values: Any) -> list[float]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if len(array) != 3:
        _fail("invalid_reference_material")
    return [_number(value) for value in array]


def _matrix(values: Any) -> list[list[float]]:
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (3, 3):
        _fail("invalid_reference_material")
    return [[_number(value) for value in row] for row in array]


def _stable_pca_axes(values: Any) -> list[list[float]]:
    axes = np.asarray(values, dtype=np.float64)
    if axes.shape != (3, 3) or not np.all(np.isfinite(axes)):
        _fail("invalid_reference_material")
    # Eigenvectors are sign-ambiguous.  Fix each sign using its largest
    # component so equivalent loads do not publish opposite axes.
    for row in axes:
        pivot = int(np.argmax(np.abs(row)))
        if row[pivot] < 0.0:
            row *= -1.0
    return _matrix(axes)


def _pca_observation(frame: dict[str, Any]) -> dict[str, Any]:
    eigenvalues = np.asarray(frame["eigenvalues"], dtype=np.float64).reshape(-1)
    if eigenvalues.shape != (3,) or not np.all(np.isfinite(eigenvalues)):
        _fail("invalid_reference_material")
    scale = max(1.0, float(np.max(np.abs(eigenvalues))))
    threshold = max(
        PCA_GAP_ABSOLUTE_EPSILON,
        scale * PCA_GAP_RELATIVE_EPSILON,
    )
    gaps = eigenvalues[:-1] - eigenvalues[1:]
    if np.any(gaps <= threshold):
        return {
            "status": "ambiguous",
            "pca_axes": None,
            "eigenvalues": [_number(value) for value in eigenvalues],
        }
    return {
        "status": "stable",
        "pca_axes": _stable_pca_axes(frame["pca_axes"]),
        "eigenvalues": [_number(value) for value in eigenvalues],
    }


def _deterministic_frame(vertices: np.ndarray) -> dict[str, Any]:
    center = np.mean(vertices, axis=0, dtype=np.float64)
    centered = vertices - center
    covariance = centered.T @ centered / max(1, len(vertices) - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    return {
        "center": center,
        "pca_axes": eigenvectors[:, order].T,
        "eigenvalues": eigenvalues[order],
    }


def _bounds(lower: Any, upper: Any) -> dict[str, list[float]]:
    minimum = _vector(lower)
    maximum = _vector(upper)
    return {
        "min": minimum,
        "max": maximum,
        "size": [_number(high - low) for low, high in zip(minimum, maximum)],
    }


def _safe_path(path: str | Path) -> Path:
    """Validate the lexical part of one supervisor-owned PLY path."""

    try:
        candidate = Path(path)
    except (TypeError, ValueError, OSError):
        _fail("invalid_reference_material")

    if not candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        _fail("invalid_reference_material")
    if candidate.suffix.lower() != ".ply":
        _fail("invalid_reference_material")
    return candidate


_PLY_SCALAR_TYPES = frozenset(
    {
        "char", "uchar", "short", "ushort", "int", "uint", "float", "double",
        "int8", "uint8", "int16", "uint16", "int32", "uint32", "float32", "float64",
    }
)
_PLY_FORMATS = frozenset(
    {"ascii 1.0", "binary_little_endian 1.0", "binary_big_endian 1.0"}
)
_PLY_FACE_COUNT_TYPES = frozenset({"uchar", "uint8", "ushort", "uint16"})
_PLY_FACE_INDEX_TYPES = frozenset({"int", "uint", "int32", "uint32"})
_ASCII_FLOAT_TOKEN = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z"
)
_ASCII_INDEX_TOKEN = re.compile(r"[+-]?[0-9]+\Z")
_PLY_SCALAR_BYTES = {
    "char": 1,
    "uchar": 1,
    "short": 2,
    "ushort": 2,
    "int": 4,
    "uint": 4,
    "float": 4,
    "double": 8,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "float32": 4,
    "float64": 8,
}


def _header_fail() -> None:
    _fail("invalid_reference_material")


def _header_count(token: str, limit: int) -> int:
    if (
        not token
        or not token.isascii()
        or not token.isdigit()
        or (len(token) > 1 and token.startswith("0"))
    ):
        _header_fail()
    if len(token) > len(str(limit)):
        _fail("reference_too_complex")
    value = int(token, 10)
    if value > limit:
        _fail("reference_too_complex")
    return value


def _read_ply_header(stream: BinaryIO, file_size: int) -> tuple[str, int, int]:
    """Preflight a bounded canonical PLY header before parser allocation."""

    elements: dict[str, tuple[int, list[tuple[str, ...]]]] = {}
    element_order: list[str] = []
    current: str | None = None
    format_name: str | None = None
    for line_number in range(PLY_HEADER_MAX_LINES):
        raw = stream.readline(PLY_HEADER_MAX_LINE_BYTES + 1)
        if not raw or len(raw) > PLY_HEADER_MAX_LINE_BYTES or not raw.endswith(b"\n"):
            _header_fail()
        if stream.tell() > PLY_HEADER_MAX_BYTES:
            _header_fail()
        try:
            line = raw[:-1].decode("ascii")
        except UnicodeDecodeError:
            _header_fail()
        if line.endswith("\r"):
            line = line[:-1]
        tokens = line.split()
        if not tokens:
            _header_fail()
        keyword = tokens[0].lower()
        if line_number == 0:
            if tokens != ["ply"]:
                _header_fail()
            continue
        if keyword == "format":
            if format_name is not None or element_order or len(tokens) != 3:
                _header_fail()
            format_name = " ".join(tokens[1:])
            if format_name not in _PLY_FORMATS:
                _header_fail()
        elif keyword == "comment":
            if len(tokens) < 2:
                _header_fail()
        elif keyword == "element":
            if len(tokens) != 3 or tokens[1] not in {"vertex", "face"}:
                _header_fail()
            name = tokens[1]
            if name in elements:
                _header_fail()
            elements[name] = (_header_count(tokens[2], file_size), [])
            element_order.append(name)
            current = name
        elif keyword == "property":
            if current is None:
                _header_fail()
            if len(tokens) == 3 and tokens[1] in _PLY_SCALAR_TYPES:
                record = ("scalar", tokens[1], tokens[2])
            elif (
                len(tokens) == 5
                and tokens[1] == "list"
                and tokens[2] in _PLY_FACE_COUNT_TYPES
                and tokens[3] in _PLY_FACE_INDEX_TYPES
            ):
                record = ("list", tokens[2], tokens[3], tokens[4])
            else:
                _header_fail()
            count, properties = elements[current]
            if any(item[-1] == record[-1] for item in properties):
                _header_fail()
            properties.append(record)
        elif keyword == "end_header":
            if len(tokens) != 1:
                _header_fail()
            break
        else:
            _header_fail()
    else:
        _header_fail()

    if (
        format_name is None
        or element_order != ["vertex", "face"]
        or set(elements) != {"vertex", "face"}
        or elements["vertex"][0] == 0
        or elements["face"][0] == 0
    ):
        _header_fail()
    vertex_properties = elements["vertex"][1]
    if [item[-1] for item in vertex_properties] != ["x", "y", "z"]:
        _header_fail()
    if any(
        item[0] != "scalar"
        or item[1] not in {"float", "double", "float32", "float64"}
        for item in vertex_properties
    ):
        _header_fail()
    face_properties = elements["face"][1]
    if (
        len(face_properties) != 1
        or face_properties[0][0] != "list"
        or face_properties[0][-1] != "vertex_indices"
    ):
        _header_fail()

    vertex_count = elements["vertex"][0]
    face_count = elements["face"][0]
    if format_name == "ascii 1.0":
        vertex_record_bytes = _MIN_ASCII_VERTEX_RECORD_BYTES
        face_record_bytes = _MIN_ASCII_FACE_RECORD_BYTES
    else:
        vertex_record_bytes = sum(
            _PLY_SCALAR_BYTES[item[1]] for item in vertex_properties
        )
        face_property = face_properties[0]
        face_record_bytes = (
            _PLY_SCALAR_BYTES[face_property[1]]
            + 3 * _PLY_SCALAR_BYTES[face_property[2]]
        )
    remaining_bytes = file_size - stream.tell()
    minimum_body_bytes = (
        vertex_count * vertex_record_bytes + face_count * face_record_bytes
    )
    if format_name == "ascii 1.0":
        # The final text row may legally end at EOF without a newline.
        minimum_body_bytes -= 1
    if (
        remaining_bytes < 0
        or minimum_body_bytes > remaining_bytes
    ):
        _fail("reference_too_complex")
    assert format_name is not None
    return format_name, vertex_count, face_count


def _read_ascii_row(stream: BinaryIO, *, final_row: bool) -> list[str]:
    """Read one bounded ASCII PLY data row without accepting hidden columns."""

    raw = stream.readline(PLY_HEADER_MAX_LINE_BYTES + 1)
    if not raw or len(raw) > PLY_HEADER_MAX_LINE_BYTES:
        _fail("invalid_reference_material")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
    elif not final_row:
        _fail("invalid_reference_material")
    try:
        tokens = raw.decode("ascii").split()
    except UnicodeDecodeError:
        _fail("invalid_reference_material")
    if not tokens:
        _fail("invalid_reference_material")
    return tokens


def _validate_ascii_body(
    stream: BinaryIO,
    vertex_count: int,
    face_count: int,
) -> None:
    """Validate every declared ASCII record before invoking the PLY parser."""

    total_rows = vertex_count + face_count
    for row_number in range(total_rows):
        tokens = _read_ascii_row(stream, final_row=row_number == total_rows - 1)
        if row_number < vertex_count:
            if len(tokens) != 3:
                _fail("invalid_reference_material")
            for token in tokens:
                if _ASCII_FLOAT_TOKEN.fullmatch(token) is None:
                    _fail("invalid_reference_material")
            continue
        if len(tokens) != 4 or tokens[0] != "3":
            _fail("invalid_reference_material")
        for token in tokens[1:]:
            if _ASCII_INDEX_TOKEN.fullmatch(token) is None:
                _fail("invalid_reference_material")

    while chunk := stream.read(8192):
        if chunk.strip():
            _fail("invalid_reference_material")


def _is_reparse_point(metadata: os.stat_result) -> bool:
    """Return whether a Windows directory-entry identity is a reparse point."""

    # 0x0400 is the Windows FILE_ATTRIBUTE_REPARSE_POINT value.  The named
    # constant is present on supported Windows Pythons, but the fallback keeps
    # the fail-closed check intact on older stdlib builds and in simulations.
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(flag and getattr(metadata, "st_file_attributes", 0) & flag)


def _is_regular_reference_file(metadata: os.stat_result) -> bool:
    """Keep the descriptor guard's regular-file/no-follow contract portable."""

    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not _is_reparse_point(metadata)
    )


def _reference_file_identity(metadata: os.stat_result) -> tuple[object, ...]:
    """Capture identity and mutable metadata used by the read race guard."""

    return tuple(
        getattr(metadata, field, None)
        for field in (
            "st_dev",
            "st_ino",
            "st_nlink",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            # Python 3.12's Windows lstat() exposes creation time as
            # st_ctime_ns, while fstat() exposes metadata-change time there.
            # st_birthtime_ns is the creation-time field shared by both APIs.
            "st_birthtime_ns",
            "st_file_attributes",
        )
    )


def _same_reference_file_identity(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return _reference_file_identity(first) == _reference_file_identity(second)


def _same_reference_file_snapshot(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    """Compare two snapshots produced by the same stat API.

    ``st_ctime_ns`` has different meanings for Windows ``lstat`` and
    ``fstat``.  It remains useful for detecting a mutation when both
    snapshots came from the same API, so keep that check at those call sites.
    """

    return _same_reference_file_identity(first, second) and (
        getattr(first, "st_ctime_ns", None)
        == getattr(second, "st_ctime_ns", None)
    )


def _reference_windows_platform() -> bool:
    """Small platform seam kept private so Windows behavior can be exercised."""

    return os.name == "nt"


def _open_reference_descriptor(path: Path) -> tuple[int, os.stat_result]:
    """Open a regular PLY without following a path replacement.

    POSIX uses the existing descriptor-level ``O_NOFOLLOW`` guard.  Windows
    does not expose that flag through ``os.open``; there we reject symlink and
    reparse-point metadata before opening, then bind the resulting descriptor
    back to that exact identity before any bytes are read.  A later descriptor
    check in ``_load_ply`` covers mutation during parsing.
    """

    windows = _reference_windows_platform()
    expected: os.stat_result | None = None
    if windows:
        try:
            expected = os.lstat(os.fspath(path))
        except OSError:
            _fail("invalid_reference_material")
        if not _is_regular_reference_file(expected) or expected.st_size > MAX_REFERENCE_BYTES:
            _fail("invalid_reference_material")

    flags = os.O_RDONLY
    if not windows:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            _fail("invalid_reference_material")
        flags |= nofollow
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    descriptor: int | None = None
    try:
        descriptor = os.open(os.fspath(path), flags)
        metadata = os.fstat(descriptor)
        if (
            not _is_regular_reference_file(metadata)
            or metadata.st_size > MAX_REFERENCE_BYTES
            or (expected is not None and not _same_reference_file_identity(expected, metadata))
        ):
            _fail("invalid_reference_material")
        if expected is not None:
            current = os.lstat(os.fspath(path))
            if (
                not _is_regular_reference_file(current)
                or not _same_reference_file_snapshot(expected, current)
                or not _same_reference_file_identity(current, metadata)
            ):
                _fail("invalid_reference_material")
        return descriptor, metadata
    except ReferenceCapabilityError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _fail("invalid_reference_material")


def _load_ply(path: Path) -> trimesh.Trimesh:
    """Open, preflight, and parse one PLY through one no-follow descriptor."""

    descriptor: int | None = None
    stream: BinaryIO | None = None
    try:
        descriptor, metadata = _open_reference_descriptor(path)
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = None
        format_name, vertex_count, face_count = _read_ply_header(
            stream, metadata.st_size
        )
        if format_name == "ascii 1.0":
            _validate_ascii_body(stream, vertex_count, face_count)
        stream.seek(0)
        mesh = trimesh.load(
            stream,
            file_type="ply",
            resolver={},
            allow_remote=False,
            force="mesh",
            process=False,
            skip_materials=True,
        )
        final_metadata = os.fstat(stream.fileno())
        if (
            not _is_regular_reference_file(final_metadata)
            or final_metadata.st_size > MAX_REFERENCE_BYTES
            or not _same_reference_file_snapshot(metadata, final_metadata)
        ):
            _fail("invalid_reference_material")
    except ReferenceCapabilityError:
        raise
    except Exception:
        _fail("invalid_reference_material")
    finally:
        if stream is not None:
            stream.close()
        if descriptor is not None:
            os.close(descriptor)
    if (
        not isinstance(mesh, trimesh.Trimesh)
        or len(mesh.vertices) == 0
        or (len(mesh.vertices), len(mesh.faces)) != (vertex_count, face_count)
    ):
        _fail("invalid_reference_material")
    return mesh


def _validate_mesh(mesh: Any) -> tuple[np.ndarray, np.ndarray]:
    try:
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        _fail("invalid_reference_material")

    if (
        vertices.ndim != 2
        or vertices.shape[1] != 3
        or len(vertices) == 0
        or faces.ndim != 2
        or faces.shape[1] != 3
        or len(faces) == 0
        or not np.all(np.isfinite(vertices))
    ):
        _fail("invalid_reference_material")
    try:
        face_indices = np.asarray(faces, dtype=np.int64)
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_reference_material")
    if not np.array_equal(faces, face_indices):
        _fail("invalid_reference_material")
    if np.any(face_indices < 0) or np.any(face_indices >= len(vertices)):
        _fail("invalid_reference_material")
    lower = CANONICAL_MIN - BOUNDARY_EPSILON
    upper = CANONICAL_MAX + BOUNDARY_EPSILON
    if np.any(vertices < lower) or np.any(vertices > upper):
        _fail("noncanonical_reference")

    canonical_vertices = np.clip(vertices, CANONICAL_MIN, CANONICAL_MAX)
    triangles = canonical_vertices[face_indices]
    cross = np.cross(
        triangles[:, 1] - triangles[:, 0],
        triangles[:, 2] - triangles[:, 0],
    )
    if not np.all(np.isfinite(cross)):
        _fail("invalid_reference_material")
    if np.any(np.all(cross == 0.0, axis=1)):
        _fail("invalid_reference_material")
    return canonical_vertices, face_indices


def _canonical_response(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_reference_material")
    if len(encoded) > MAX_RESPONSE_BYTES:
        _fail("response_too_large")
    return value


class ReferenceCapability:
    """Expose fixed, bounded observations for one supervisor-owned PLY."""

    __slots__ = ("_reference_id", "_mesh", "_vertices", "_summary")

    def __init__(self, reference_id: str, reference_path: str | Path):
        if (
            type(reference_id) is not str
            or len(reference_id) > MAX_REFERENCE_ID_LENGTH
            or _REFERENCE_ID.fullmatch(reference_id) is None
        ):
            _fail("invalid_reference")
        path = _safe_path(reference_path)
        mesh = _load_ply(path)
        vertices, faces = _validate_mesh(mesh)
        self._reference_id = reference_id
        self._mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        self._vertices = vertices
        self._summary: dict[str, Any] | None = None

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle one closed JSON-shaped request and return one observation."""

        if not _is_dict(request) or set(request) != {
            "schema",
            "reference_id",
            "method",
            "args",
        }:
            _fail("invalid_request")
        if request["schema"] != REQUEST_SCHEMA:
            _fail("invalid_request")
        if request["reference_id"] != self._reference_id:
            _fail("invalid_reference")
        method = request["method"]
        if type(method) is not str:
            _fail("invalid_request")
        if method in _PROHIBITED_METHODS:
            _fail("unsupported_operation")
        if method != "summary":
            _fail("unknown_method")
        args = request["args"]
        if not _is_dict(args):
            _fail("invalid_request")

        try:
            if method == "summary":
                if args:
                    _fail("invalid_request")
                observation = self._summary_observation()
        except ReferenceCapabilityError:
            raise
        except (
            AttributeError,
            FloatingPointError,
            IndexError,
            KeyError,
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            RuntimeError,
        ):
            _fail("invalid_reference_material")

        return _canonical_response(
            {
                "schema": RESPONSE_SCHEMA,
                "reference_id": self._reference_id,
                "method": method,
                "observation": observation,
            }
        )

    def _summary_observation(self) -> dict[str, Any]:
        if self._summary is None:
            stats = _compute_stats(self._mesh)
            quality = _compute_quality(self._mesh)
            frame = _deterministic_frame(self._vertices)
            self._summary = {
                "schema": SUMMARY_SCHEMA,
                "coordinate_contract": COORDINATE_CONTRACT,
                "stats": {
                    "vertices": int(stats["vertices"]),
                    "faces": int(stats["faces"]),
                    "edges": int(stats["edges"]),
                    "bounds": _bounds(
                        stats["bounding_box"]["min"],
                        stats["bounding_box"]["max"],
                    ),
                    "surface_area": _number(stats["surface_area"]),
                    "volume": (
                        _number(stats["volume"])
                        if stats["volume"] is not None
                        else None
                    ),
                },
                "quality": {
                    "watertight": bool(quality["watertight"]),
                    "volume_valid": bool(quality["volume_valid"]),
                    "degenerate_faces": int(quality["degenerate_faces"]),
                    "euler_number": int(quality["euler_number"]),
                },
                "canonical_frame": {
                    "center": _vector(frame["center"]),
                    **_pca_observation(frame),
                },
            }
        # The cached value is an implementation detail; never let a caller's
        # mutation alter a later observation.
        return deepcopy(self._summary)

__all__ = ["ReferenceCapability", "ReferenceCapabilityError"]
