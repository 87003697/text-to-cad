"""Fail-closed regression tests for the installed-plugin smoke.

These tests exercise the pure validation logic — manifest computation, parity
assertion, critical-runtime presence, entrypoint containment, and PATH/PYTHON*
sanitization — without invoking the real Codex CLI. They guard the cases the
smoke exists to catch: a missing materialized runtime, content or file-set
divergence between the prepared tree and the installed cache, and a silent
fallback to the source checkout.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = REPO_ROOT / "scripts/release/smoke_installed_plugin.py"


def _load_smoke_module():
    module_name = "text_to_cad_smoke_installed_plugin"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def _load_workspace_core():
    module_name = "text_to_cad_smoke_workspace_core"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = (
        REPO_ROOT
        / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ManifestTests(unittest.TestCase):
    def test_deterministic_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b").write_text("beta")
            (root / "a").write_text("alpha")
            (root / "nested").mkdir()
            (root / "nested" / "z").write_text("zed")
            manifest = smoke.compute_manifest(root)
            self.assertEqual(
                [e.path for e in manifest.entries],
                ["a", "b", "nested/z"],
            )
            # Digest is stable across runs.
            second = smoke.compute_manifest(root)
            self.assertEqual(manifest.digest, second.digest)

    def test_rejects_symlink_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").write_text("hi")
            os.symlink("real", root / "link")
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.compute_manifest(root)
            self.assertIn("symlink", str(ctx.exception).lower())

    def test_private_mode_is_allowed_only_for_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.toml"
            config.write_text("[plugins]\n")
            config.chmod(0o600)

            with self.assertRaises(smoke.SmokeError):
                smoke.compute_manifest(root)

            manifest = smoke.compute_manifest(
                root,
                private_paths=("config.toml",),
            )
            self.assertEqual(manifest.entries[0].mode, "0600")


class ManifestParityTests(unittest.TestCase):
    def _tree(self, contents: dict[str, str]) -> Path:
        tmp = Path(tempfile.mkdtemp())
        for rel, body in contents.items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return tmp

    def test_parity_passes_when_identical(self) -> None:
        a = self._tree({"pkg/__init__.py": "x", "README": "y"})
        b = self._tree({"pkg/__init__.py": "x", "README": "y"})
        try:
            smoke.assert_manifests_equal(
                smoke.compute_manifest(a), smoke.compute_manifest(b)
            )
        finally:
            self._rm(a)
            self._rm(b)

    def test_missing_file_fails_closed(self) -> None:
        prepared = self._tree({"pkg/__init__.py": "x", "README": "y"})
        installed = self._tree({"pkg/__init__.py": "x"})
        try:
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_manifests_equal(
                    smoke.compute_manifest(prepared),
                    smoke.compute_manifest(installed),
                )
            self.assertIn("dropped by installer", str(ctx.exception))
            self.assertIn("README", str(ctx.exception))
        finally:
            self._rm(prepared)
            self._rm(installed)

    def test_content_mismatch_fails_closed(self) -> None:
        prepared = self._tree({"pkg/__init__.py": "released"})
        installed = self._tree({"pkg/__init__.py": "different"})
        try:
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_manifests_equal(
                    smoke.compute_manifest(prepared),
                    smoke.compute_manifest(installed),
                )
            self.assertIn("content mismatch", str(ctx.exception))
        finally:
            self._rm(prepared)
            self._rm(installed)

    def test_extra_installed_file_fails_closed(self) -> None:
        prepared = self._tree({"pkg/__init__.py": "x"})
        installed = self._tree({"pkg/__init__.py": "x", "surprise": "s"})
        try:
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_manifests_equal(
                    smoke.compute_manifest(prepared),
                    smoke.compute_manifest(installed),
                )
            self.assertIn("added by installer", str(ctx.exception))
        finally:
            self._rm(prepared)
            self._rm(installed)

    @staticmethod
    def _rm(path: Path) -> None:
        import shutil
        shutil.rmtree(path, ignore_errors=True)


class CriticalRuntimeTests(unittest.TestCase):
    def _populate_all_runtimes(self, root: Path) -> None:
        for runtime_dir, probe_rel in smoke.CRITICAL_RUNTIME_PATHS:
            probe = root / runtime_dir / probe_rel
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("stub")

    def test_all_present_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_all_runtimes(root)
            verified = smoke.assert_critical_runtimes(root)
            self.assertEqual(
                {v["runtime"] for v in verified},
                {r for r, _ in smoke.CRITICAL_RUNTIME_PATHS},
            )

    def test_missing_runtime_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_all_runtimes(root)
            # Drop the cad-viewer runtime as if Codex silently omitted the
            # tracked symlink to viewer/.
            import shutil
            shutil.rmtree(root / "skills/cad-viewer")
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_critical_runtimes(root)
            self.assertIn("critical runtime is missing", str(ctx.exception))
            self.assertIn("cad-viewer", str(ctx.exception))

    def test_empty_runtime_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_all_runtimes(root)
            # Preserve the directory but drop the probe file — the failure
            # mode of a "stub" install that materialized only shells.
            probe = root / "skills/cad/scripts/packages/cadgen/src/cadgen/__init__.py"
            probe.unlink()
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_critical_runtimes(root)
            self.assertIn("materialized empty", str(ctx.exception))


class EntrypointContainmentTests(unittest.TestCase):
    def test_entrypoint_inside_installed_root_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = root / "skills/cad/scripts/canonical-build/__main__.py"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
            smoke.assert_entrypoint_under(root, entry)

    def test_entrypoint_outside_installed_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_other:
            installed = Path(tmp_root)
            source_like = Path(tmp_other)
            entry = source_like / "skills/cad/scripts/canonical-build/__main__.py"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_entrypoint_under(installed, entry)
            self.assertIn("escaped installed cache", str(ctx.exception))

    def test_installed_path_outside_isolated_home_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_home, tempfile.TemporaryDirectory() as tmp_other:
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_installed_path_is_isolated(
                    Path(tmp_home), Path(tmp_other) / "plugins/cad"
                )
            self.assertIn("escaped isolated state", str(ctx.exception))

    def test_installed_path_inside_isolated_home_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_home:
            installed = Path(tmp_home) / "plugins/cache/cad"
            installed.mkdir(parents=True)
            self.assertEqual(
                installed.resolve(),
                smoke.assert_installed_path_is_isolated(Path(tmp_home), installed),
            )


class EnvSanitizationTests(unittest.TestCase):
    def test_python_path_vars_are_dropped(self) -> None:
        env_before = {"PYTHONPATH": "/repo/src", "PYTHONHOME": "/repo", "PATH": "/usr/bin"}
        original = os.environ.copy()
        os.environ.clear()
        os.environ.update(env_before)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sanitized = smoke.sanitize_env_for_installed_run(
                    Path(tmp) / "install",
                    Path(tmp) / "source",
                    python_executable=Path(sys.executable),
                )
            self.assertNotIn("PYTHONPATH", sanitized)
            self.assertNotIn("PYTHONHOME", sanitized)
            self.assertEqual(sanitized["PYTHONNOUSERSITE"], "1")
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_path_entries_under_source_root_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            install = Path(tmp) / "install"
            install.mkdir()
            spoof_path = os.pathsep.join([
                str(source / "scripts"),
                "/usr/bin",
                str(source / "bin"),
                "/opt/homebrew/bin",
            ])
            original = os.environ.copy()
            os.environ.clear()
            os.environ.update({"PATH": spoof_path})
            try:
                sanitized = smoke.sanitize_env_for_installed_run(
                    install, source, python_executable=Path(sys.executable)
                )
                self.assertNotIn(str(source / "scripts"), sanitized["PATH"])
                self.assertNotIn(str(source / "bin"), sanitized["PATH"])
                self.assertIn("/usr/bin", sanitized["PATH"])
                self.assertIn("/opt/homebrew/bin", sanitized["PATH"])
            finally:
                os.environ.clear()
                os.environ.update(original)


class ReceiptShapeTests(unittest.TestCase):
    def test_success_receipt_binds_source_installed_and_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prepared = Path(tmp) / "prepared"
            installed = Path(tmp) / "installed"
            prepared.mkdir()
            installed.mkdir()
            (prepared / "a").write_text("x")
            (installed / "a").write_text("x")
            prepared_manifest = smoke.compute_manifest(prepared)
            installed_manifest = smoke.compute_manifest(installed)
            receipt = smoke.build_receipt(
                source_root=Path(tmp),
                source_sha="deadbeef",
                prepared_tree=prepared,
                prepared_manifest=prepared_manifest,
                codex_version_string="codex-cli 0.147.0",
                install_result={
                    "install": {
                        "pluginId": "cad@text-to-cad",
                        "installedPath": str(installed),
                    },
                    "marketplace": {"marketplaceName": "text-to-cad"},
                },
                installed_root=installed,
                installed_manifest=installed_manifest,
                critical_runtimes=[{"runtime": "example", "probe": "example/x", "probe_sha256": "a" * 64}],
                agent_source_projection={
                    "schema": "text-to-cad.agent-source-projection/1",
                    "version": "1",
                    "digest": "b" * 64,
                    "entry_count": 20,
                },
                registered_build_probe={
                    "resolved_entrypoint": str(installed / "cli"),
                    "build_exit_code": 0,
                },
                argv=["--source-root", tmp],
                codex_home=Path(tmp) / "codex-home",
            )
        self.assertEqual(receipt["schema"], smoke.RECEIPT_SCHEMA)
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["source"]["git_sha"], "deadbeef")
        self.assertEqual(receipt["prepared_tree"]["digest_sha256"], prepared_manifest.digest)
        self.assertEqual(receipt["installed"]["digest_sha256"], installed_manifest.digest)
        for name in (
            "prepared_tree_symlink_free",
            "installed_tree_symlink_free",
            "manifest_parity",
            "critical_runtimes_present",
            "entrypoint_under_installed_root",
            "source_checkout_hidden_from_installed_run",
            "isolated_python_sys_path_source_free",
            "registered_build_completed",
            "agent_source_projection_present",
        ):
            self.assertIs(receipt["assertions"][name], True)
        self.assertEqual(
            receipt["agent_source_projection"]["schema"],
            "text-to-cad.agent-source-projection/1",
        )

    def test_tool_registry_binds_installed_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rebuild = root / "canonical.py"
            geometry = root / "geometry.py"
            rebuild.write_text("rebuild")
            geometry.write_text("geometry")
            registry = smoke._tool_registry(rebuild, geometry)
            rebuild_sha256 = smoke._hash_file(rebuild)
            geometry_sha256 = smoke._hash_file(geometry)
        self.assertEqual(registry["schema"], "mesh-to-cad.tool-registry/2")
        self.assertEqual(
            registry["rebuild"]["entrypoint"],
            "/workspace/repo/skills/cad/scripts/canonical-build/__main__.py",
        )
        self.assertEqual(
            registry["geometry"]["entrypoint"],
            "/workspace/repo/skills/mesh-compare/scripts/mesh-compare/__main__.py",
        )
        self.assertEqual(
            registry["rebuild"]["entrypoint_sha256"],
            rebuild_sha256,
        )
        self.assertEqual(
            registry["geometry"]["entrypoint_sha256"],
            geometry_sha256,
        )
        self.assertEqual(len(registry["identity_sha256"]), 64)

    def test_tool_registry_matches_installed_workspace_schema(self) -> None:
        """The smoke registry must use the runner's canonical sandbox paths."""

        workspace_core = _load_workspace_core()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rebuild = root / "canonical.py"
            geometry = root / "geometry.py"
            rebuild.write_text("rebuild")
            geometry.write_text("geometry")
            registry = smoke._tool_registry(rebuild, geometry)

            try:
                workspace_core._validate_tool_registry_document(registry)
            except workspace_core.WorkspaceError as exc:
                self.fail(f"smoke registry rejected by shipped Workspace: {exc}")

        self.assertEqual(
            registry["rebuild"]["entrypoint"],
            "/workspace/repo/skills/cad/scripts/canonical-build/__main__.py",
        )
        self.assertEqual(
            registry["geometry"]["entrypoint"],
            "/workspace/repo/skills/mesh-compare/scripts/mesh-compare/__main__.py",
        )

    def test_sys_path_rejects_source_checkout_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "repo"
            source.mkdir()
            with self.assertRaises(smoke.SmokeError) as ctx:
                smoke.assert_sys_path_source_free(
                    ["/usr/lib/python", str(source / "packages/meshscope/src")],
                    source,
                )
        self.assertIn("sys.path reaches the source checkout", str(ctx.exception))

    def test_sys_path_accepts_task_private_dependency_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "repo"
            isolated = Path(tmp) / "isolated/site-packages"
            source.mkdir()
            isolated.mkdir(parents=True)
            self.assertEqual(
                [str(isolated.resolve())],
                smoke.assert_sys_path_source_free([str(isolated)], source),
            )

    def test_failure_receipt_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = smoke.build_failure_receipt(
                source_root=Path(tmp),
                argv=["--source-root", tmp],
                detail="something went wrong",
            )
        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["error"], "something went wrong")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
