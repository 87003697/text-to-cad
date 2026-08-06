"""Strict `.vbsvo` binary codec for complete immutable surface snapshots."""

from __future__ import annotations

from pathlib import Path
import struct

import numpy as np

from meshscope.voxblame.errors import SurfaceTreeError
from meshscope.voxblame.tree import (
    CHILD_ORDER_XYZ,
    SurfaceTree,
    validate_depth,
)


MAGIC = b"VBSV"
VERSION = 1
HAS_SUBTREE_SPANS = 1
STORAGE_SCHEMA = "voxblame.svo/1"
_HEADER = struct.Struct("<4sBBBBQQ32s")
_UINT32_LE = np.dtype("<u4")
_UINT32_MAX = (1 << 32) - 1


def encode_surface_tree(tree: SurfaceTree) -> bytes:
    """Encode a validated tree into the stable `.vbsvo` v1 byte format."""
    digest = bytes.fromhex(tree.logical_sha256)
    header = _HEADER.pack(
        MAGIC,
        VERSION,
        tree.max_depth,
        CHILD_ORDER_XYZ,
        HAS_SUBTREE_SPANS,
        tree.node_count,
        tree.leaf_count,
        digest,
    )
    return header + tree.masks + tree.spans.tobytes()


def decode_surface_tree(data: bytes) -> SurfaceTree:
    """Decode snapshot bytes and fail closed on any structural inconsistency."""
    if not isinstance(data, bytes):
        raise SurfaceTreeError("surface-tree snapshot must be bytes")
    if len(data) < _HEADER.size:
        raise SurfaceTreeError("surface-tree snapshot is truncated")
    magic, version, depth, order, flags, nodes, leaves, digest = _HEADER.unpack_from(
        data
    )
    if magic != MAGIC or version != VERSION:
        raise SurfaceTreeError("unsupported surface-tree format")
    if order != CHILD_ORDER_XYZ or flags != HAS_SUBTREE_SPANS:
        raise SurfaceTreeError("unsupported surface-tree child order or flags")
    validate_depth(depth)
    if nodes < 1 or nodes > _UINT32_MAX:
        raise SurfaceTreeError("surface-tree node count is invalid")
    expected = _HEADER.size + nodes * 5
    if len(data) != expected:
        raise SurfaceTreeError("surface-tree snapshot length is invalid")
    mask_start = _HEADER.size
    masks = data[mask_start : mask_start + nodes]
    spans = np.frombuffer(
        data,
        dtype=_UINT32_LE,
        count=nodes,
        offset=mask_start + nodes,
    )
    tree = SurfaceTree(depth, masks, spans, int(leaves))
    if bytes.fromhex(tree.logical_sha256) != digest:
        raise SurfaceTreeError("surface-tree logical digest mismatch")
    return tree


def write_surface_tree(tree: SurfaceTree, path: str | Path) -> None:
    """Write a complete snapshot; workflow-level atomicity is handled by the store."""
    snapshot = Path(path)
    try:
        snapshot.write_bytes(encode_surface_tree(tree))
    except OSError as exc:
        raise SurfaceTreeError(
            f"failed to write surface-tree snapshot: {snapshot}"
        ) from exc


def read_surface_tree(path: str | Path) -> SurfaceTree:
    """Read and strictly validate a `.vbsvo` snapshot."""
    snapshot = Path(path)
    try:
        data = snapshot.read_bytes()
    except OSError as exc:
        raise SurfaceTreeError(
            f"failed to read surface-tree snapshot: {snapshot}"
        ) from exc
    return decode_surface_tree(data)
