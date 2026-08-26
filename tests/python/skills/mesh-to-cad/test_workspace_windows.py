"""Windows-safe seams for the Agent tree copy guard.

The test process is usually POSIX, so the private platform seam is forced to
the Windows implementation.  The files are still real filesystem objects;
only the unavailable Windows descriptor APIs are avoided by the implementation
under test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
WORKSPACE_DIR = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
WORKSPACE_PATH = WORKSPACE_DIR / "workspace.py"
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))


def _load_workspace():
    spec = importlib.util.spec_from_file_location(
        "mesh_to_cad_workspace_windows_tests", WORKSPACE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load workspace facade")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WindowsAgentTreeCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = _load_workspace()
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-tree-windows-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _copy(self, source: Path, target: Path) -> None:
        with mock.patch.object(
            self.workspace, "_agent_windows_platform", return_value=True
        ):
            self.workspace._copy_agent_tree(source, target)

    def test_nested_regular_tree_copies_without_directory_descriptors(self) -> None:
        source = self.root / "source"
        (source / "nested/deeper").mkdir(parents=True)
        (source / "root.txt").write_bytes(b"root")
        (source / "nested/deeper/child.txt").write_bytes(b"child")
        target = self.root / "target"

        self._copy(source, target)

        self.assertEqual(b"root", (target / "root.txt").read_bytes())
        self.assertEqual(
            b"child", (target / "nested/deeper/child.txt").read_bytes()
        )

    def test_windows_lstat_and_fstat_creation_time_fields_compare_stably(self) -> None:
        """Windows lstat/fstat expose different meanings for st_ctime_ns."""

        def metadata(*, ctime: int) -> SimpleNamespace:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_size=1,
                st_nlink=1,
                st_dev=11,
                st_ino=22,
                st_mtime_ns=33,
                st_ctime_ns=ctime,
                st_birthtime_ns=44,
                st_file_attributes=0,
            )

        expected = metadata(ctime=101)
        opened = metadata(ctime=202)
        current = metadata(ctime=101)
        with (
            mock.patch.object(self.workspace, "_agent_lstat", side_effect=(expected, current)),
            mock.patch.object(self.workspace.os, "open", return_value=17),
            mock.patch.object(self.workspace.os, "fstat", return_value=opened),
        ):
            descriptor, result = self.workspace._agent_open_windows_file(
                self.root / "regular.txt"
            )

        self.assertEqual(17, descriptor)
        self.assertIs(expected, result)

    def test_windows_target_is_opened_in_binary_mode(self) -> None:
        """The Windows CRT must not expand LF bytes while copying."""

        source = self.root / "source.txt"
        source.write_bytes(b"line one\nline two\n")
        target = self.root / "target.txt"
        source_fd = self.workspace.os.open(source, self.workspace.os.O_RDONLY)
        binary_flag = 0x8000
        opened_flags: list[int] = []
        original_open = self.workspace.os.open

        def capture_target_open(path, flags, *args, **kwargs):
            if Path(path) == target:
                opened_flags.append(flags)
                # The POSIX test host does not know the Windows CRT flag, so
                # strip only the fabricated bit that would otherwise make
                # the local open fail.  A real Windows run must keep
                # O_BINARY on the fd so the test exercises the CRT contract.
                if self.workspace.os.name != "nt":
                    flags &= ~binary_flag
            return original_open(path, flags, *args, **kwargs)

        try:
            with mock.patch.object(
                self.workspace.os, "O_BINARY", binary_flag, create=True
            ), mock.patch.object(
                self.workspace.os, "open", side_effect=capture_target_open
            ):
                copied = self.workspace._copy_agent_file_from_descriptor(
                    source_fd, target
                )
        finally:
            self.workspace.os.close(source_fd)

        self.assertEqual(len(b"line one\nline two\n"), copied)
        self.assertEqual([binary_flag], [flags & binary_flag for flags in opened_flags])
        self.assertEqual(b"line one\nline two\n", target.read_bytes())

    def test_symlink_is_rejected_and_partial_target_is_removed(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "safe.txt").write_bytes(b"safe")
        link = source / "link.txt"
        try:
            link.symlink_to(source / "safe.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        target = self.root / "target"

        with self.assertRaises(self.workspace.WorkspaceError) as raised:
            self._copy(source, target)

        self.assertEqual("invalid_workspace_path", raised.exception.classification)
        self.assertFalse(target.exists())

    def test_reparse_metadata_is_rejected_for_directory_and_file(self) -> None:
        flag = getattr(self.workspace.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        directory = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_file_attributes=flag,
        )
        regular = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=flag,
            st_nlink=1,
            st_size=1,
        )
        with mock.patch.object(
            self.workspace.stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            flag,
            create=True,
        ):
            with self.assertRaises(self.workspace.WorkspaceError):
                self.workspace._agent_validate_directory_stat(directory)
            with self.assertRaises(self.workspace.WorkspaceError):
                self.workspace._agent_validate_file_stat(regular)

    def test_hardlink_is_rejected(self) -> None:
        source = self.root / "source"
        source.mkdir()
        original = self.root / "original.txt"
        original.write_bytes(b"hardlink")
        try:
            (source / "linked.txt").hardlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("hardlinks are unavailable")
        target = self.root / "target"

        with self.assertRaises(self.workspace.WorkspaceError):
            self._copy(source, target)
        self.assertFalse(target.exists())

    def test_tree_byte_and_file_count_limits_clean_up(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "first.txt").write_bytes(b"1234")
        (source / "second.txt").write_bytes(b"5678")

        with mock.patch.object(self.workspace, "_AGENT_MAX_TREE_BYTES", 7):
            with self.assertRaises(self.workspace.WorkspaceError):
                self._copy(source, self.root / "byte-target")
        self.assertFalse((self.root / "byte-target").exists())

        with mock.patch.object(self.workspace, "_AGENT_MAX_TREE_FILES", 1):
            with self.assertRaises(self.workspace.WorkspaceError):
                self._copy(source, self.root / "count-target")
        self.assertFalse((self.root / "count-target").exists())

    def test_file_identity_mutation_after_open_is_rejected_and_cleaned(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "mutable.txt").write_bytes(b"mutable")
        target = self.root / "target"
        original_fstat = self.workspace.os.fstat
        calls = 0

        def changed_after_copy(fd):
            nonlocal calls
            calls += 1
            metadata = original_fstat(fd)
            # Windows open binding is call 1, copy preflight call 2, the
            # copied-metadata snapshot call 3, and the final race check call 4.
            if calls == 4:
                values = list(metadata)
                values[6] += 1
                return self.workspace.os.stat_result(values)
            return metadata

        with (
            mock.patch.object(
                self.workspace, "_agent_windows_platform", return_value=True
            ),
            mock.patch.object(
                self.workspace.os, "fstat", side_effect=changed_after_copy
            ),
        ):
            with self.assertRaises(self.workspace.WorkspaceError) as raised:
                self.workspace._copy_agent_tree(source, target)

        self.assertEqual("invalid_workspace_path", raised.exception.classification)
        self.assertFalse(target.exists())

    def test_directory_identity_mutation_is_rejected_and_cleaned(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "file.txt").write_bytes(b"contents")
        target = self.root / "target"
        original_lstat = self.workspace.os.lstat
        source_lstat_calls = 0

        def changed_final_lstat(path, **kwargs):
            nonlocal source_lstat_calls
            metadata = original_lstat(path, **kwargs)
            if kwargs:
                return metadata
            if Path(path) == source:
                source_lstat_calls += 1
                if source_lstat_calls == 3:
                    values = list(metadata)
                    values[8] += 1
                    return self.workspace.os.stat_result(values)
            return metadata

        with (
            mock.patch.object(
                self.workspace, "_agent_windows_platform", return_value=True
            ),
            mock.patch.object(
                self.workspace.os, "lstat", side_effect=changed_final_lstat
            ),
        ):
            with self.assertRaises(self.workspace.WorkspaceError):
                self.workspace._copy_agent_tree(source, target)
        self.assertFalse(target.exists())

    def test_root_replacement_before_first_traversal_is_rejected_without_copy(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "original.txt").write_bytes(b"original")
        replacement = self.root / "replacement"
        replacement.mkdir()
        (replacement / "replacement.txt").write_bytes(b"replacement")
        target = self.root / "target"
        original_lstat = self.workspace._agent_lstat
        source_snapshots = 0

        def replace_after_outer_validation(path: Path):
            nonlocal source_snapshots
            metadata = original_lstat(path)
            if path == source:
                source_snapshots += 1
                if source_snapshots == 1:
                    source.rename(self.root / "original-source")
                    replacement.rename(source)
            return metadata

        with (
            mock.patch.object(
                self.workspace, "_agent_windows_platform", return_value=True
            ),
            mock.patch.object(
                self.workspace, "_agent_lstat", side_effect=replace_after_outer_validation
            ),
            mock.patch.object(
                self.workspace,
                "_copy_agent_file_from_descriptor",
            ) as copy_file,
        ):
            with self.assertRaises(self.workspace.WorkspaceError) as raised:
                self.workspace._copy_agent_tree(source, target)

        self.assertEqual("invalid_workspace_path", raised.exception.classification)
        self.assertFalse(target.exists())
        copy_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
