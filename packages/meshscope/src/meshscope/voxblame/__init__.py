"""Hierarchical surface-error localization for iterative CAD reconstruction."""

from meshscope.voxblame.codec import (
    STORAGE_SCHEMA,
    decode_surface_tree,
    encode_surface_tree,
    read_surface_tree,
    write_surface_tree,
)
from meshscope.voxblame.contracts import (
    BOUNDARY_EPSILON,
    COORDINATE_CONTRACT,
    FORBIDDEN_FIELDS,
    MAX_DEPTH,
    REPORT_REQUIRED_FIELDS,
    SESSION_REQUIRED_FIELDS,
    SUMMARY_REQUIRED_FIELDS,
    validate_contract_bundle,
    validate_report_contract,
    validate_session_contract,
    validate_summary_contract,
)
from meshscope.voxblame.errors import (
    UNSUPPORTED_OR_INVALID_STATE,
    OctreeError,
    SurfaceTreeError,
    UnsupportedOrInvalidVoxBlameState,
    VoxBlameError,
)
from meshscope.voxblame.frame import CanonicalFrame
from meshscope.voxblame.grading import (
    ChangeCell,
    ErrorCell,
    NextAction,
    RegionHandle,
    compare_error_trees,
    grade_surface_trees,
    lattice_bounds,
    select_next_action,
    world_bounds,
)
from meshscope.voxblame.session import run_step
from meshscope.voxblame.tree import SurfaceTree, tree_from_codes
from meshscope.voxblame.voxelize import build_lattice_tree, voxelize_mesh

__all__ = [
    "CanonicalFrame",
    "ChangeCell",
    "ErrorCell",
    "NextAction",
    "OctreeError",
    "UnsupportedOrInvalidVoxBlameState",
    "UNSUPPORTED_OR_INVALID_STATE",
    "BOUNDARY_EPSILON",
    "COORDINATE_CONTRACT",
    "FORBIDDEN_FIELDS",
    "MAX_DEPTH",
    "REPORT_REQUIRED_FIELDS",
    "SESSION_REQUIRED_FIELDS",
    "SUMMARY_REQUIRED_FIELDS",
    "RegionHandle",
    "STORAGE_SCHEMA",
    "SurfaceTree",
    "SurfaceTreeError",
    "VoxBlameError",
    "build_lattice_tree",
    "compare_error_trees",
    "decode_surface_tree",
    "encode_surface_tree",
    "grade_surface_trees",
    "lattice_bounds",
    "read_surface_tree",
    "run_step",
    "select_next_action",
    "tree_from_codes",
    "voxelize_mesh",
    "validate_contract_bundle",
    "validate_report_contract",
    "validate_session_contract",
    "validate_summary_contract",
    "world_bounds",
    "write_surface_tree",
]
