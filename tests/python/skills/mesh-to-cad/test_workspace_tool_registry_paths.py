"""Cross-platform coverage for tool-registry entrypoint path predicates.

Published runner registries carry the fixed POSIX ``/workspace/repo/skills``
namespace even when validation runs on Windows. Host-native paths belong to
separately defined host fields and are covered by their own predicate. The
serialized field rejects relative paths, traversal segments, foreign-flavored
spellings and malformed roots. See
``skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
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


class SandboxEntrypointPredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = _load_workspace_core()
        self.check = self.core._is_canonical_sandbox_absolute_path

    def test_accepts_the_fixed_posix_sandbox_namespace(self) -> None:
        self.assertTrue(
            self.check(
                "/workspace/repo/skills/cad/scripts/canonical-build/__main__.py"
            )
        )
        self.assertTrue(
            self.check(
                "/workspace/repo/skills/mesh-compare/scripts/mesh-compare/__main__.py"
            )
        )

    def test_rejects_host_drive_and_unc_spellings(self) -> None:
        self.assertFalse(self.check(r"C:\workspace\repo\skills\tool.py"))
        self.assertFalse(self.check(r"\\server\share\workspace\repo\skills\tool.py"))

    def test_rejects_mixed_separators_and_traversal(self) -> None:
        self.assertFalse(self.check("/workspace/repo/skills\\cad/tool.py"))
        self.assertFalse(self.check("/workspace/repo/skills/../tool.py"))
        self.assertFalse(self.check("/workspace/repo//skills/tool.py"))
        self.assertFalse(self.check("/workspace/repo/skills/tool.py/"))

    def test_rejects_namespace_root_without_entrypoint(self) -> None:
        self.assertFalse(self.check("/workspace/repo"))
        self.assertFalse(self.check("/workspace/repo/skills"))

    def test_exact_sandbox_paths_reach_digest_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-windows-") as directory:
            root = Path(directory)
            rebuild = root / "rebuild.py"
            geometry = root / "geometry.py"
            rebuild.write_bytes(b"rebuild")
            geometry.write_bytes(b"geometry")
            value = {
                "schema": "mesh-to-cad.tool-registry/2",
                "rebuild": {
                    "id": "cad.canonical-build/1",
                    "entrypoint": "/workspace/repo/skills/cad/scripts/canonical-build/__main__.py",
                    "entrypoint_sha256": "0" * 64,
                },
                "geometry": {
                    "id": "mesh-compare.voxblame/1",
                    "entrypoint": "/workspace/repo/skills/mesh-compare/scripts/mesh-compare/__main__.py",
                    "entrypoint_sha256": "1" * 64,
                },
            }
            value["identity_sha256"] = self.core._identity(
                "mesh-to-cad.tool-registry/2", value
            )
            registry = root / "registry.json"
            registry.write_text(
                json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaises(self.core.WorkspaceError) as raised:
                self.core._load_tool_registry(
                    registry,
                    rebuild_entrypoint=rebuild,
                    geometry_entrypoint=geometry,
                )
            self.assertEqual("untrusted_tool", raised.exception.classification)
            self.assertEqual(
                "$.tool_registry.rebuild.entrypoint_sha256", raised.exception.path
            )


class SerializedRegistryEntrypointTests(unittest.TestCase):
    """The serialized field is a fixed runner namespace, never a host path."""

    _REBUILD_ENTRYPOINT = (
        "/workspace/repo/skills/cad/scripts/canonical-build/__main__.py"
    )
    _GEOMETRY_ENTRYPOINT = (
        "/workspace/repo/skills/mesh-compare/scripts/mesh-compare/__main__.py"
    )

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def _registry(self, rebuild_entrypoint: str) -> dict:
        value = {
            "schema": "mesh-to-cad.tool-registry/2",
            "rebuild": {
                "id": "cad.canonical-build/1",
                "entrypoint": rebuild_entrypoint,
                "entrypoint_sha256": "0" * 64,
            },
            "geometry": {
                "id": "mesh-compare.voxblame/1",
                "entrypoint": self._GEOMETRY_ENTRYPOINT,
                "entrypoint_sha256": "1" * 64,
            },
        }
        value["identity_sha256"] = self.core._identity(
            "mesh-to-cad.tool-registry/2", value
        )
        return value

    def test_rejects_host_and_smuggled_entrypoint_spellings(self) -> None:
        attacks = (
            r"C:\arbitrary\tool.py",
            r"\\server\share\arbitrary\tool.py",
            "/tmp/arbitrary/tool.py",
            "/workspace/repo/skills/cad\\scripts/canonical-build/__main__.py",
            "/workspace/repo/skills/../outside.py",
            "/workspace/repo/skills-evil/tool.py",
        )
        for attack in attacks:
            with self.subTest(entrypoint=attack):
                with self.assertRaises(self.core.WorkspaceError) as raised:
                    self.core._validate_tool_registry_document(
                        self._registry(attack)
                    )
                self.assertEqual("untrusted_tool", raised.exception.classification)
                self.assertEqual(
                    "$.tool_registry.rebuild.entrypoint", raised.exception.path
                )


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
    """Host-native fields dispatch using the running platform's spelling."""

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
