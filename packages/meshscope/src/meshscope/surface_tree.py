"""Compatibility imports for the structured :mod:`meshscope.voxblame` API."""

from meshscope.voxblame.codec import (
    CHILD_ORDER_XYZ,
    HAS_SUBTREE_SPANS,
    MAGIC,
    STORAGE_SCHEMA,
    VERSION,
    decode_surface_tree,
    encode_surface_tree,
    read_surface_tree,
    write_surface_tree,
)
from meshscope.voxblame.errors import SurfaceTreeError
from meshscope.voxblame.tree import SurfaceTree, tree_from_codes
from meshscope.voxblame.voxelize import build_lattice_tree as build_surface_tree

__all__ = [
    "CHILD_ORDER_XYZ",
    "HAS_SUBTREE_SPANS",
    "MAGIC",
    "STORAGE_SCHEMA",
    "SurfaceTree",
    "SurfaceTreeError",
    "VERSION",
    "build_surface_tree",
    "decode_surface_tree",
    "encode_surface_tree",
    "read_surface_tree",
    "tree_from_codes",
    "write_surface_tree",
]
