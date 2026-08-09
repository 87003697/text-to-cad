"""Error hierarchy shared by the VoxBlame subsystem."""


class VoxBlameError(ValueError):
    """Base class for invalid geometry, trees, reports, and persisted state."""


UNSUPPORTED_OR_INVALID_STATE = "unsupported_or_invalid_voxblame_state"


class UnsupportedOrInvalidVoxBlameState(VoxBlameError):
    """One public classification for unsupported or structurally invalid state."""

    classification = UNSUPPORTED_OR_INVALID_STATE

    def __init__(self, *, path: str, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(self.classification)


class OctreeError(VoxBlameError):
    """Backward-compatible workflow error raised by VoxBlame operations."""


class SurfaceTreeError(OctreeError):
    """Raised for invalid surface-tree structure or snapshot bytes."""
