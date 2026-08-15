"""Public fail-closed tests for the mounted Agent browser surface scanner."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import tempfile
import unittest

from scripts.pilot import browser_surface


class BrowserSurfaceTests(unittest.TestCase):
    """Exercise the scanner through real filesystem trees and its OS adapter."""

    def test_required_and_optional_missing_roots_are_distinct(self) -> None:
        """A vanished exact mount closes; an explicitly optional root may be absent."""

        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [(missing, Path("/sandbox/required"), True)]
                )
            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [(missing, Path("/sandbox/optional"), False)]
                ),
                [],
            )

    def test_every_symlink_is_closed_or_resolved_once_inside_root(self) -> None:
        """Dangling, escaping, and cyclic links close without outside traversal."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "surface"
            outside = Path(temp) / "outside"
            root.mkdir()
            outside.mkdir()
            cases = {
                "dangling": "missing",
                "escaping": os.fspath(outside),
            }
            for name, target in cases.items():
                link = root / name
                link.symlink_to(target)
                with self.subTest(name=name), self.assertRaises(
                    browser_surface.BrowserSurfaceError
                ):
                    browser_surface.discover_browser_roots(
                        [(root, Path("/sandbox"), True)]
                    )
                link.unlink()

            (root / "cycle-a").symlink_to("cycle-b")
            (root / "cycle-b").symlink_to("cycle-a")
            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [(root, Path("/sandbox"), True)]
                )

    def test_browser_alias_and_marker_reached_through_symlink_are_detected(self) -> None:
        """A renamed in-root target cannot hide behind a browser-named link."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "surface"
            target = root / "libexec/vendor-render"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"\x7fELF" + b"\0" * 64)
            target.chmod(0o755)
            (root / "bin").mkdir()
            (root / "bin/chromium").symlink_to("../libexec/vendor-render")

            findings = browser_surface.discover_browser_roots(
                [(root, Path("/sandbox"), True)]
            )

        self.assertEqual(
            findings,
            [
                {
                    "kind": "executable",
                    "target": "/sandbox/libexec/vendor-render",
                    "mask": "dev-null",
                }
            ],
        )

    def test_os_boundary_errors_are_never_suppressed(self) -> None:
        """EACCES from lstat, open, read, or scandir reaches the public closure."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "surface"
            root.mkdir()
            (root / "file").write_bytes(b"plain")

            for operation in ("lstat", "open", "read", "scandir"):
                with self.subTest(operation=operation):
                    adapter = _DeniedOperationFilesystem(operation)
                    with self.assertRaises(browser_surface.BrowserSurfaceError):
                        browser_surface.discover_browser_roots(
                            [(root, Path("/sandbox"), True)],
                            filesystem=adapter,
                        )


class _DeniedOperationFilesystem(browser_surface.SurfaceFilesystem):
    """Delegate to the real OS except for one deterministic EACCES boundary."""

    def __init__(self, denied: str) -> None:
        self.denied = denied

    @staticmethod
    def _deny(operation: str) -> None:
        raise PermissionError(errno.EACCES, f"forced {operation} denial")

    def lstat(self, path, *, dir_fd=None):
        if self.denied == "lstat":
            self._deny("lstat")
        return super().lstat(path, dir_fd=dir_fd)

    def open(self, path, flags, *, dir_fd=None):
        if self.denied == "open":
            self._deny("open")
        return super().open(path, flags, dir_fd=dir_fd)

    def read(self, descriptor, size):
        if self.denied == "read":
            self._deny("read")
        return super().read(descriptor, size)

    def scandir(self, descriptor):
        if self.denied == "scandir":
            self._deny("scandir")
        return super().scandir(descriptor)


if __name__ == "__main__":
    unittest.main()
