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
from meshscope.voxblame.measurement import (
    MEASUREMENT_SCHEMA,
    MEASUREMENT_SUMMARY_SCHEMA,
    MeasureStepResult,
    measure_step,
)
from meshscope.voxblame.prepare_reference import (
    CANONICAL_REFERENCE_SCHEMA,
    NORMALIZATION_SCHEMA,
    PREPARE_FAILURE_SCHEMA,
    PrepareReferenceError,
    PrepareReferenceResult,
    prepare_reference,
    publish_prepare_failure,
)
from meshscope.voxblame.preview import (
    PREVIEW_SCHEMA,
    PreviewScene,
    PublishPreviewResult,
    ValidatedPreviewIdentity,
    prepare_preview_scene,
    publish_preview,
    validate_preview_identity,
)
from meshscope.voxblame.region_diff import (
    REGION_DIFF_SCHEMA,
    REPAIR_BATCH_SCHEMA,
    RegionDiffResult,
    publish_region_diff,
    validate_region_diff_contract,
)
from meshscope.voxblame.targets import (
    active_repair_depth,
    inspect_repair_frontier,
    page_repair_targets,
    project_target_local_occupancy,
)
from meshscope.voxblame.tree import SurfaceTree, tree_from_codes
from meshscope.voxblame.verification import (
    VERIFICATION_SCHEMA,
    VerifyStepResult,
    verify_step,
)
from meshscope.voxblame.voxelize import build_lattice_tree, voxelize_mesh

__all__ = [
    "CanonicalFrame",
    "CANONICAL_REFERENCE_SCHEMA",
    "OctreeError",
    "UnsupportedOrInvalidVoxBlameState",
    "UNSUPPORTED_OR_INVALID_STATE",
    "BOUNDARY_EPSILON",
    "COORDINATE_CONTRACT",
    "FORBIDDEN_FIELDS",
    "MAX_DEPTH",
    "MEASUREMENT_SCHEMA",
    "MEASUREMENT_SUMMARY_SCHEMA",
    "MeasureStepResult",
    "NORMALIZATION_SCHEMA",
    "PREPARE_FAILURE_SCHEMA",
    "PREVIEW_SCHEMA",
    "PrepareReferenceError",
    "PrepareReferenceResult",
    "PreviewScene",
    "PublishPreviewResult",
    "ValidatedPreviewIdentity",
    "VERIFICATION_SCHEMA",
    "VerifyStepResult",
    "REGION_DIFF_SCHEMA",
    "REPAIR_BATCH_SCHEMA",
    "RegionDiffResult",
    "REPORT_REQUIRED_FIELDS",
    "SESSION_REQUIRED_FIELDS",
    "SUMMARY_REQUIRED_FIELDS",
    "STORAGE_SCHEMA",
    "SurfaceTree",
    "SurfaceTreeError",
    "VoxBlameError",
    "build_lattice_tree",
    "active_repair_depth",
    "decode_surface_tree",
    "encode_surface_tree",
    "measure_step",
    "inspect_repair_frontier",
    "page_repair_targets",
    "project_target_local_occupancy",
    "read_surface_tree",
    "prepare_reference",
    "prepare_preview_scene",
    "publish_preview",
    "validate_preview_identity",
    "publish_prepare_failure",
    "publish_region_diff",
    "validate_region_diff_contract",
    "tree_from_codes",
    "voxelize_mesh",
    "validate_contract_bundle",
    "validate_report_contract",
    "validate_session_contract",
    "validate_summary_contract",
    "verify_step",
    "write_surface_tree",
]
