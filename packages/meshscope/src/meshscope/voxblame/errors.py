"""Error hierarchy shared by the VoxBlame subsystem."""


class VoxBlameError(ValueError):
    """Base class for invalid geometry, trees, reports, and persisted state."""


class OctreeError(VoxBlameError):
    """Backward-compatible workflow error raised by VoxBlame operations."""


class SurfaceTreeError(OctreeError):
    """Raised for invalid surface-tree structure or snapshot bytes."""
