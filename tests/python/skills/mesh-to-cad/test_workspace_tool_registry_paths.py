"""Cross-platform coverage for the tool-registry entrypoint path predicate.

The registry entrypoint is executed on the host that ran ``finalize`` -- a
POSIX box in CI, a Windows workstation for offline reviews -- so the
predicate must accept native absolute paths for the running platform while
rejecting relative paths, traversal segments, foreign-flavored spellings
and malformed roots. Regression: the previous
``entrypoint.startswith("/")`` gate turned every Windows registry into an
``untrusted_tool`` failure. See
``skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
WORKSPACE_CORE_PATH = (
    REPO_ROOT
    / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py"
)


def _load_workspace_core():
    spec = importlib.util.spec_from_file_location(
        "mesh_to_cad_workspace_core", WORKSPACE_CORE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load workspace_core")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WindowsAbsolutePathPredicateTests(unittest.TestCase):
    """Windows-flavored predicate exercised from any host.

    On non-Windows CI we call the Windows-specific helper directly so the
    Windows contract is verified even when ``os.name != "nt"``. On the
    actual Windows runner ``_is_canonical_absolute_path`` dispatches to the
    same helper for native paths.
    """

    def setUp(self) -> None:
        self.core = _load_workspace_core()
        self.check = self.core._is_canonical_windows_absolute_path

    def test_accepts_drive_letter_absolute_path(self) -> None:
        self.assertTrue(self.check(r"D:\repo\tool.py"))
        self.assertTrue(self.check(r"C:\Users\runner\work\tool.py"))

    def test_accepts_unc_absolute_path(self) -> None:
        self.assertTrue(self.check(r"\\server\share\tool.py"))

    def test_rejects_relative_path(self) -> None:
        self.assertFalse(self.check(r"repo\tool.py"))
        self.assertFalse(self.check("tool.py"))

    def test_rejects_forward_slashes(self) -> None:
        self.assertFalse(self.check("D:/repo/tool.py"))
        self.assertFalse(self.check("/repo/tool.py"))

    def test_rejects_traversal_segments(self) -> None:
        self.assertFalse(self.check(r"D:\repo\..\tool.py"))

    def test_rejects_drive_letter_without_backslash(self) -> None:
        self.assertFalse(self.check(r"D:tool.py"))

    def test_rejects_truncated_unc_anchor(self) -> None:
        self.assertFalse(self.check(r"\\server"))
        self.assertFalse(self.check(r"\\server\\"))

    def test_rejects_null_byte(self) -> None:
        self.assertFalse(self.check("D:\\repo\\tool.py\x00"))


class PosixAbsolutePathPredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = _load_workspace_core()
        self.check = self.core._is_canonical_posix_absolute_path

    def test_accepts_leading_slash_absolute_path(self) -> None:
        self.assertTrue(self.check("/repo/tool.py"))

    def test_rejects_relative_path(self) -> None:
        self.assertFalse(self.check("repo/tool.py"))

    def test_rejects_double_slash_root(self) -> None:
        self.assertFalse(self.check("//repo/tool.py"))

    def test_rejects_backslashes(self) -> None:
        self.assertFalse(self.check(r"/repo\tool.py"))
        self.assertFalse(self.check(r"C:\repo\tool.py"))

    def test_rejects_traversal_segments(self) -> None:
        self.assertFalse(self.check("/repo/../tool.py"))

    def test_rejects_trailing_slash(self) -> None:
        # ``PurePosixPath.as_posix()`` collapses trailing slashes; the
        # canonical spelling equality catches that divergence.
        self.assertFalse(self.check("/repo/tool.py/"))


class HostDispatchTests(unittest.TestCase):
    """``_is_canonical_absolute_path`` selects the native predicate for the
    running host and does not accept the foreign flavor unattended."""

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def test_dispatch_matches_running_os(self) -> None:
        if os.name == "nt":
            self.assertTrue(self.core._is_canonical_absolute_path(r"D:\repo\tool.py"))
            # A POSIX-only path must not slip through on Windows.
            self.assertFalse(
                self.core._is_canonical_absolute_path("/repo/tool.py")
            )
        else:
            self.assertTrue(self.core._is_canonical_absolute_path("/repo/tool.py"))
            # A Windows drive-letter path is not a native absolute path on
            # POSIX -- accepting it would let a registry crafted for the
            # wrong host reach the process.
            self.assertFalse(
                self.core._is_canonical_absolute_path(r"D:\repo\tool.py")
            )


if __name__ == "__main__":
    unittest.main()
