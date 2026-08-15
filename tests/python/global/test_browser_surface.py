"""Public fail-closed tests for the mounted Agent browser surface scanner."""

from __future__ import annotations

import errno
import inspect
import os
from pathlib import Path
import tempfile
import unittest

from scripts.pilot import browser_surface


class BrowserSurfaceTests(unittest.TestCase):
    """Exercise the scanner through real filesystem trees and its OS adapter."""

    def test_scanner_has_no_dangling_link_bypass(self) -> None:
        """Every dangling link remains fail-closed on every scanner surface."""

        self.assertNotIn(
            "permitted_dangling_symlink_roots",
            inspect.signature(browser_surface.discover_browser_roots).parameters,
        )

    def test_declared_root_closure_rejects_undeclared_transit(self) -> None:
        """A link chain cannot leave declared roots and return at its final inode."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usr = root / "usr"
            etc = root / "etc"
            undeclared = root / "tmp"
            usr.mkdir()
            etc.mkdir()
            undeclared.mkdir()
            target = usr / "target"
            target.write_text("inert", encoding="utf-8")
            (usr / "alias").symlink_to(etc / "alias")
            (etc / "alias").symlink_to(undeclared / "alias")
            (undeclared / "alias").symlink_to(target)

            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [
                        (usr, Path("/sandbox/usr"), True),
                        (etc, Path("/sandbox/etc"), True),
                    ],
                    permitted_symlink_roots=[usr, etc],
                )

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

    def test_directory_self_alias_is_inert_unless_browser_shaped(self) -> None:
        """Distro compatibility aliases do not hide browser-named directories."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "usr"
            binary = root / "bin"
            binary.mkdir(parents=True)
            compatibility = binary / "X11"
            compatibility.symlink_to(".")

            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [(root, Path("/sandbox/usr"), True)]
                ),
                [],
            )

            compatibility.unlink()
            (binary / "chromium").symlink_to(".")
            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [(root, Path("/sandbox/usr"), True)]
                ),
                [
                    {
                        "kind": "package",
                        "target": "/sandbox/usr/bin/chromium",
                        "mask": "tmpfs",
                    }
                ],
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "surface"
            (root / "a").mkdir(parents=True)
            (root / "b").mkdir()
            (root / "a/to-b").symlink_to("../b")
            (root / "b/to-a").symlink_to("../a")
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
            cache_payload = root / "libexec/cache-payload"
            cache_payload.write_text("Google Chrome cache payload", encoding="utf-8")
            (root / "cache").mkdir()
            (root / "cache/renamed-product").symlink_to("../libexec/cache-payload")

            filesystem = _CountingFilesystem(
                {"vendor-render", "cache-payload"}
            )
            findings = browser_surface.discover_browser_roots(
                [(root, Path("/sandbox"), True)],
                filesystem=filesystem,
            )
            repeated = browser_surface.discover_browser_roots(
                [(root, Path("/sandbox"), True)]
            )

        self.assertEqual(
            findings,
            [
                {
                    "kind": "cache",
                    "target": "/sandbox/libexec",
                    "mask": "tmpfs",
                }
            ],
        )
        self.assertEqual(repeated, findings)
        self.assertEqual(
            filesystem.file_opens,
            {"vendor-render": 1, "cache-payload": 1},
        )

    def test_declared_read_only_roots_close_cross_root_aliases(self) -> None:
        """Image roots may cross-link, but aliases and cycles remain closed."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usr = root / "usr"
            etc = root / "etc"
            (usr / "bin").mkdir(parents=True)
            (etc / "alternatives").mkdir(parents=True)
            target = etc / "alternatives/vendor-render"
            target.write_bytes(b"\x7fELF" + b"\0" * 64)
            target.chmod(0o755)
            (usr / "bin/chromium").symlink_to(target)
            certificate = usr / "share/certs/vendor.pem"
            certificate.parent.mkdir(parents=True)
            certificate.write_text("certificate", encoding="utf-8")
            (etc / "certs").mkdir()
            (etc / "certs/current").symlink_to(certificate)
            (etc / "certs/hash.0").symlink_to("current")
            package_target = etc / "alternatives/vendor-package"
            package_target.write_text('{"name":"playwright"}', encoding="utf-8")
            (usr / "lib/vendor").mkdir(parents=True)
            (usr / "lib/vendor/package.json").symlink_to(package_target)

            findings = browser_surface.discover_browser_roots(
                [(usr, Path("/sandbox/usr"), True), (etc, Path("/sandbox/etc"), True)],
                permitted_symlink_roots=[usr, etc],
            )

            self.assertEqual(
                findings,
                [
                    {
                        "kind": "executable",
                        "target": "/sandbox/usr/bin/chromium",
                        "mask": "dev-null",
                    },
                    {
                        "kind": "package",
                        "target": "/sandbox/usr/lib/vendor",
                        "mask": "tmpfs",
                    }
                ],
            )

            (usr / "cycle").symlink_to(etc / "cycle")
            (etc / "cycle").symlink_to(usr / "cycle")
            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [
                        (usr, Path("/sandbox/usr"), True),
                        (etc, Path("/sandbox/etc"), True),
                    ],
                    permitted_symlink_roots=[usr, etc],
                )

    def test_cross_root_alias_may_canonically_return_to_source_root(self) -> None:
        """Alternatives-style chains are resolved even when their final inode returns."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usr = root / "usr"
            etc = root / "etc"
            binary = usr / "bin"
            alternatives = etc / "alternatives"
            binary.mkdir(parents=True)
            alternatives.mkdir(parents=True)
            target = binary / "mawk"
            target.write_bytes(b"\x7fELF" + b"\0" * 64)
            target.chmod(0o755)
            (alternatives / "awk").symlink_to(target)
            (binary / "awk").symlink_to(alternatives / "awk")

            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [
                        (usr, Path("/sandbox/usr"), True),
                        (etc, Path("/sandbox/etc"), True),
                    ],
                    permitted_symlink_roots=[usr, etc],
                ),
                [],
            )

            (alternatives / "chromium").symlink_to(target)
            (binary / "chromium").symlink_to(alternatives / "chromium")
            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [
                        (usr, Path("/sandbox/usr"), True),
                        (etc, Path("/sandbox/etc"), True),
                    ],
                    permitted_symlink_roots=[usr, etc],
                ),
                [
                    {
                        "kind": "executable",
                        "target": "/sandbox/etc/alternatives/chromium",
                        "mask": "dev-null",
                    },
                    {
                        "kind": "executable",
                        "target": "/sandbox/usr/bin/chromium",
                        "mask": "dev-null",
                    }
                ],
            )

    def test_immutable_image_roots_allow_only_non_browser_dangling_aliases(self) -> None:
        """Sealed images tolerate distro doc links, never browser-shaped aliases."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usr = root / "usr"
            etc = root / "etc"
            (usr / "share/doc/vendor").mkdir(parents=True)
            (etc / "modules-load.d").mkdir(parents=True)
            (usr / "share/doc/vendor/NEWS.gz").symlink_to("missing-news.gz")
            (etc / "modules-load.d/modules.conf").symlink_to("../modules")

            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [(usr, Path("/usr"), True), (etc, Path("/etc"), True)],
                    permitted_symlink_roots=[usr, etc],
                )

            self.assertEqual(
                browser_surface.discover_browser_roots(
                    [(usr, Path("/usr"), True), (etc, Path("/etc"), True)],
                    permitted_symlink_roots=[usr, etc],
                    permitted_dangling_symlink_roots=[usr, etc],
                ),
                [],
            )

            (usr / "bin").mkdir()
            (usr / "bin/chromium").symlink_to("missing-browser")
            with self.assertRaises(browser_surface.BrowserSurfaceError):
                browser_surface.discover_browser_roots(
                    [(usr, Path("/usr"), True), (etc, Path("/etc"), True)],
                    permitted_symlink_roots=[usr, etc],
                    permitted_dangling_symlink_roots=[usr, etc],
                )

    def test_os_boundary_errors_are_never_suppressed(self) -> None:
        """EACCES from lstat, open, read, or scandir reaches the public closure."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "surface"
            root.mkdir()
            (root / "file").write_bytes(b"plain")
            (root / "file-link").symlink_to("file")

            for operation in (
                "lstat",
                "open",
                "fstat",
                "read",
                "scandir",
                "readlink",
            ):
                with self.subTest(operation=operation):
                    adapter = _DeniedOperationFilesystem(operation)
                    with self.assertRaises(browser_surface.BrowserSurfaceError):
                        browser_surface.discover_browser_roots(
                            [(root, Path("/sandbox"), True)],
                            filesystem=adapter,
                        )

    def test_duplicate_target_kind_is_deterministic_across_mount_order(self) -> None:
        """Exact duplicate masks select one stable kind independent of traversal."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            (first / "ms-playwright").mkdir(parents=True)
            (second / "ms-playwright").mkdir(parents=True)
            target = Path("/sandbox/ms-playwright")
            forward = browser_surface.discover_browser_roots(
                [(first, Path("/sandbox"), True), (second, Path("/sandbox"), True)]
            )
            reverse = browser_surface.discover_browser_roots(
                [(second, Path("/sandbox"), True), (first, Path("/sandbox"), True)]
            )

        expected = [
            {"kind": "cache", "target": target.as_posix(), "mask": "tmpfs"}
        ]
        self.assertEqual(forward, expected)
        self.assertEqual(reverse, expected)


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

    def fstat(self, descriptor):
        if self.denied == "fstat":
            self._deny("fstat")
        return super().fstat(descriptor)

    def scandir(self, descriptor):
        if self.denied == "scandir":
            self._deny("scandir")
        return super().scandir(descriptor)

    def readlink(self, path, *, dir_fd=None):
        if self.denied == "readlink":
            self._deny("readlink")
        return super().readlink(path, dir_fd=dir_fd)


class _CountingFilesystem(browser_surface.SurfaceFilesystem):
    """Count exact regular targets while delegating every operation to the OS."""

    def __init__(self, names: set[str]) -> None:
        self.file_opens = {name: 0 for name in names}

    def open(self, path, flags, *, dir_fd=None):
        name = os.fspath(path)
        if name in self.file_opens:
            self.file_opens[name] += 1
        return super().open(path, flags, dir_fd=dir_fd)


if __name__ == "__main__":
    unittest.main()
