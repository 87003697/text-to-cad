"""Compatibility facade for the structured :mod:`meshscope.voxblame` API.

New production code should import from ``meshscope.voxblame``. Morton leaf
helpers intentionally live only in the test support package and are not part
of this production facade.
"""

from meshscope.voxblame.errors import OctreeError
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
from meshscope.voxblame.voxelize import voxelize_mesh

build_surface_tree = voxelize_mesh
grade_trees = grade_surface_trees

__all__ = [
    "CanonicalFrame",
    "ChangeCell",
    "ErrorCell",
    "NextAction",
    "OctreeError",
    "RegionHandle",
    "build_surface_tree",
    "compare_error_trees",
    "grade_surface_trees",
    "grade_trees",
    "lattice_bounds",
    "run_step",
    "select_next_action",
    "voxelize_mesh",
    "world_bounds",
]
