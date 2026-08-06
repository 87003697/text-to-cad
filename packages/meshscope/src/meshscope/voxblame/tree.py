"""Immutable logical representation of conservative surface occupancy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Iterator

import numpy as np

from meshscope.voxblame.errors import SurfaceTreeError


CHILD_ORDER_XYZ = 0
LOGICAL_SCHEMA = "voxblame.svo/1"
_DIGEST_DOMAIN = b"voxblame.svo/1\0"
_UINT32_LE = np.dtype("<u4")
_UINT64_LE = np.dtype("<u8")
_UINT32_MAX = (1 << 32) - 1


@dataclass(frozen=True)
class SurfaceTree:
    """Preorder internal-node masks plus a validated subtree index."""

    max_depth: int
    masks: bytes
    spans: np.ndarray
    leaf_count: int

    def __post_init__(self) -> None:
        validate_depth(self.max_depth)
        if not isinstance(self.masks, bytes):
            raise SurfaceTreeError("surface-tree masks must be bytes")
        spans = np.asarray(self.spans)
        if spans.ndim != 1 or spans.dtype.str != "<u4":
            raise SurfaceTreeError("surface-tree spans must be one-dimensional <u4")
        if len(self.masks) != len(spans) or not self.masks:
            raise SurfaceTreeError(
                "surface-tree masks and spans must have equal non-zero length"
            )
        if len(self.masks) > _UINT32_MAX:
            raise SurfaceTreeError("surface-tree node count exceeds uint32")
        canonical_spans, canonical_leaves = derive_index(
            self.max_depth, self.masks
        )
        if not np.array_equal(spans, canonical_spans):
            raise SurfaceTreeError("surface-tree subtree spans are invalid")
        if (
            not isinstance(self.leaf_count, (int, np.integer))
            or isinstance(self.leaf_count, (bool, np.bool_))
            or int(self.leaf_count) != canonical_leaves
        ):
            raise SurfaceTreeError("surface-tree leaf count is invalid")
        span_bytes = np.ascontiguousarray(spans, dtype=_UINT32_LE).tobytes()
        object.__setattr__(self, "spans", np.frombuffer(span_bytes, dtype=_UINT32_LE))
        object.__setattr__(self, "leaf_count", canonical_leaves)

    @property
    def node_count(self) -> int:
        return len(self.masks)

    @property
    def logical_sha256(self) -> str:
        return hashlib.sha256(
            _DIGEST_DOMAIN
            + bytes((self.max_depth, CHILD_ORDER_XYZ))
            + self.masks
        ).hexdigest()

    @classmethod
    def from_masks(cls, max_depth: int, masks: bytes) -> "SurfaceTree":
        spans, leaf_count = derive_index(max_depth, masks)
        return cls(max_depth, masks, spans, leaf_count)

    @classmethod
    def empty(cls, max_depth: int) -> "SurfaceTree":
        return cls.from_masks(max_depth, b"\0")

    def child_node(self, node: int, depth: int, child: int) -> int | None:
        """Return an occupied internal child index, or ``None`` for a leaf."""
        if not 0 <= node < self.node_count:
            raise SurfaceTreeError("surface-tree node index is invalid")
        if not 0 <= depth < self.max_depth:
            raise SurfaceTreeError("surface-tree node depth is invalid")
        if not 0 <= child < 8:
            raise SurfaceTreeError("surface-tree child must be in [0, 8)")
        if not (self.masks[node] & (1 << child)):
            return None
        if depth + 1 == self.max_depth:
            return None
        cursor = node + 1
        for sibling in range(child):
            if self.masks[node] & (1 << sibling):
                cursor += int(self.spans[cursor])
        return cursor

    def child_occupied(self, node: int, child: int) -> bool:
        if not 0 <= node < self.node_count or not 0 <= child < 8:
            raise SurfaceTreeError("surface-tree child lookup is invalid")
        return bool(self.masks[node] & (1 << child))

    def iter_leaf_codes(self) -> Iterator[int]:
        """Yield max-depth Morton leaves for tests and bounded debugging."""

        def visit(node: int, depth: int, prefix: int) -> Iterator[int]:
            for child in range(8):
                if not self.child_occupied(node, child):
                    continue
                child_prefix = (prefix << 3) | child
                if depth + 1 == self.max_depth:
                    yield child_prefix
                    continue
                child_node = self.child_node(node, depth, child)
                if child_node is None:
                    raise SurfaceTreeError("occupied internal child has no node")
                yield from visit(child_node, depth + 1, child_prefix)

        if self.masks[0]:
            yield from visit(0, 0, 0)

    def leaf_codes(self) -> np.ndarray:
        """Materialize leaves for compatibility with existing tests/debuggers."""
        return np.fromiter(self.iter_leaf_codes(), dtype=_UINT64_LE)


def tree_from_codes(
    values: Iterable[int] | np.ndarray, max_depth: int
) -> SurfaceTree:
    """Build a canonical tree from max-depth Morton leaves for tests."""
    validate_depth(max_depth)
    array = np.asarray(list(values) if not isinstance(values, np.ndarray) else values)
    if array.ndim == 1 and not len(array):
        return SurfaceTree.empty(max_depth)
    if array.ndim != 1 or array.dtype.kind not in "iu":
        raise SurfaceTreeError(
            "Morton oracle values must be a one-dimensional integer array"
        )
    if array.dtype.kind == "i" and np.any(array < 0):
        raise SurfaceTreeError("Morton oracle values cannot be negative")
    codes = np.unique(array.astype(_UINT64_LE, copy=False))
    if int(codes[-1]) >= 1 << (3 * max_depth):
        raise SurfaceTreeError("Morton oracle value exceeds max_depth")
    masks = bytearray()

    def visit(subset: np.ndarray, depth: int) -> None:
        node = len(masks)
        masks.append(0)
        shift = 3 * (max_depth - depth - 1)
        child_values = (subset >> np.uint64(shift)) & np.uint64(7)
        for child in range(8):
            selected = subset[child_values == child]
            if not len(selected):
                continue
            masks[node] |= 1 << child
            if depth + 1 < max_depth:
                visit(selected, depth + 1)

    visit(codes, 0)
    return SurfaceTree.from_masks(max_depth, bytes(masks))


def derive_index(max_depth: int, masks: bytes) -> tuple[np.ndarray, int]:
    """Derive and validate subtree spans and max-depth occupied leaf count."""
    validate_depth(max_depth)
    if not isinstance(masks, bytes) or not masks:
        raise SurfaceTreeError("surface-tree must contain one root mask")
    if len(masks) > _UINT32_MAX:
        raise SurfaceTreeError("surface-tree node count exceeds uint32")
    spans = np.zeros(len(masks), dtype=_UINT32_LE)

    def visit(node: int, depth: int, *, root: bool = False) -> tuple[int, int]:
        if node >= len(masks):
            raise SurfaceTreeError("surface-tree snapshot is truncated")
        mask = masks[node]
        if mask == 0 and not root:
            raise SurfaceTreeError("non-root surface-tree node cannot be empty")
        cursor = node + 1
        leaves = 0
        for child in range(8):
            if not mask & (1 << child):
                continue
            if depth + 1 == max_depth:
                leaves += 1
            else:
                child_span, child_leaves = visit(cursor, depth + 1)
                cursor += child_span
                leaves += child_leaves
        span = cursor - node
        spans[node] = span
        return span, leaves

    consumed, leaf_count = visit(0, 0, root=True)
    if consumed != len(masks):
        raise SurfaceTreeError("surface-tree snapshot has trailing nodes")
    return spans, leaf_count


def validate_depth(depth: int) -> None:
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 21:
        raise SurfaceTreeError("max_depth must be an integer in [1, 21]")
