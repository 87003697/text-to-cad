"""Unit tests for the runner ↔ browser_runtime seam.

Focuses on the greenfield gap: prepare_sandbox writing a Codex MCP config,
and build_bwrap_argv wiring the capability dir + MCP url through to bwrap.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"


def _load_runner():
    path = PILOT_ROOT / "runner.py"
    spec = importlib.util.spec_from_file_location("pilot_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_workspace_core():
    path = (
        REPO_ROOT
        / "skills"
        / "mesh-to-cad"
        / "scripts"
        / "mesh-to-cad-workspace"
        / "workspace_core.py"
    )
    spec = importlib.util.spec_from_file_location("tool_registry_workspace_core", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PrepareSandboxMcpConfigTests(unittest.TestCase):

    def test_runner_publishes_canonical_read_only_tool_registry(self):
        runner = _load_runner()
        with TemporaryDirectory() as tmp:
            authority = Path(tmp) / "authority"
            registry_path = runner.publish_tool_registry(authority)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))

            self.assertEqual(registry["schema"], "mesh-to-cad.tool-registry/2")
            self.assertEqual(registry["rebuild"]["id"], "cad.canonical-build/1")
            self.assertEqual(
                registry["rebuild"]["entrypoint"],
                str(runner.SANDBOX_CAD_REBUILD_ENTRYPOINT),
            )
            self.assertEqual(registry["geometry"]["id"], "mesh-compare.voxblame/1")
            self.assertEqual(
                registry["geometry"]["entrypoint"],
                str(runner.SANDBOX_GEOMETRY_ENTRYPOINT),
            )
            self.assertEqual(registry_path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(
                registry_path,
                authority / runner.TRUSTED_TOOL_REGISTRY_NAME,
            )

    def test_runner_registry_sandbox_paths_pass_host_validation_by_digest(self):
        runner = _load_runner()
        workspace_core = _load_workspace_core()
        with TemporaryDirectory() as tmp:
            registry_path = runner.publish_tool_registry(Path(tmp) / "authority")

            loaded = workspace_core._load_tool_registry(
                registry_path,
                rebuild_entrypoint=runner.CAD_REBUILD_ENTRYPOINT,
                geometry_entrypoint=runner.GEOMETRY_ENTRYPOINT,
            )
            self.assertEqual(
                str(runner.SANDBOX_CAD_REBUILD_ENTRYPOINT),
                loaded["rebuild"]["entrypoint"],
            )
            self.assertEqual(
                str(runner.SANDBOX_GEOMETRY_ENTRYPOINT),
                loaded["geometry"]["entrypoint"],
            )

    def test_prepare_sandbox_without_mcp_url_omits_config(self):
        from tests.python.support.authority_fixtures import build_authority

        runner = _load_runner()
        with TemporaryDirectory() as tmp:
            host_home = Path(tmp) / "home"
            host_home.mkdir()
            fixture = build_authority(host_home, dedupe_token="seam-no-mcp")
            exp = Path(tmp) / "outputs" / "job-a"
            (exp / "run").mkdir(parents=True)
            job_home = runner.prepare_job_codex_home(exp, fixture.receipt)
            self.assertTrue(job_home.is_dir())
            body = (job_home / "config.toml").read_text(encoding="utf-8")
            self.assertNotIn("[mcp_servers.browser]", body)

    def test_prepare_sandbox_writes_config_toml_with_mcp_url(self):
        from tests.python.support.authority_fixtures import build_authority

        runner = _load_runner()
        with TemporaryDirectory() as tmp:
            host_home = Path(tmp) / "home"
            host_home.mkdir()
            fixture = build_authority(host_home, dedupe_token="seam-mcp")
            exp = Path(tmp) / "outputs" / "job-b"
            (exp / "run").mkdir(parents=True)
            url = "http://127.0.0.1:59321/mcp"
            job_home = runner.prepare_job_codex_home(
                exp, fixture.receipt, browser_mcp_url=url
            )
            config_path = job_home / "config.toml"
            self.assertTrue(config_path.is_file())
            body = config_path.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.browser]", body)
            self.assertIn(f'url = "{url}"', body)
            self.assertIn('transport = "http"', body)

    def test_prepare_sandbox_installs_marketplace_source_rewrite(self):
        from tests.python.support.authority_fixtures import build_authority

        runner = _load_runner()
        plugin_deployment = runner.plugin_deployment
        with TemporaryDirectory() as tmp:
            host_home = Path(tmp) / "home"
            host_home.mkdir()
            fixture = build_authority(host_home, dedupe_token="seam-source")
            exp = Path(tmp) / "outputs" / "job-c"
            (exp / "run").mkdir(parents=True)
            job_home = runner.prepare_job_codex_home(exp, fixture.receipt)
            body = (job_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn(
                f'source = "{plugin_deployment.SANDBOX_MARKETPLACE_SOURCE}"',
                body,
            )
            self.assertNotIn(str(fixture.publish_tree), body)


class ExperimentGitHistoryTests(unittest.TestCase):
    def test_successful_finalize_compacts_but_preserves_git_history(self):
        runner = _load_runner()
        with TemporaryDirectory() as tmp:
            exp = Path(tmp) / "outputs/group/exp"
            runner.prepare_exp(exp)
            for index in range(8):
                artifact = exp / f"artifact-{index}.txt"
                artifact.write_text((f"revision-{index}\n" * 128), encoding="utf-8")
                runner.run_git(exp, ["add", artifact.name])
                runner.run_git(exp, ["commit", "--quiet", "-m", f"step {index}"])
            runner.run_git(exp, ["config", "gc.reflogExpire", "now"])
            runner.run_git(exp, ["config", "gc.reflogExpireUnreachable", "now"])
            runner.run_git(exp, ["config", "gc.pruneExpire", "now"])
            head_before = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            reflog_before = subprocess.run(
                ["git", "reflog", "--format=%H %gs"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            refs_before = subprocess.run(
                ["git", "show-ref"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            index_before = (exp / ".git/index").read_bytes()
            count_before = subprocess.run(
                ["git", "count-objects", "-v"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            upper = exp / "run/.codex-home/sessions/a/b/c"
            upper.mkdir(parents=True)
            (upper / "rollout-test.jsonl").write_text("{}\n", encoding="utf-8")

            with mock.patch.object(runner, "validate_workspace_delivery", return_value={}):
                status = runner.finalize_pilot(exp, 0, {"KEEP_STATE": "1"})

            count_after = subprocess.run(
                ["git", "count-objects", "-v"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            head_after = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            reflog_after = subprocess.run(
                ["git", "reflog", "--format=%H %gs"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            refs_after = subprocess.run(
                ["git", "show-ref"],
                cwd=exp,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            index_after = (exp / ".git/index").read_bytes()
            fsck = subprocess.run(
                ["git", "fsck", "--no-dangling"],
                cwd=exp,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, status)
        self.assertRegex(count_before, r"(?m)^count: [1-9][0-9]*$")
        self.assertRegex(count_after, r"(?m)^count: 0$")
        self.assertEqual(head_before, head_after)
        self.assertEqual(reflog_before, reflog_after)
        self.assertEqual(refs_before, refs_after)
        self.assertEqual(index_before, index_after)
        self.assertEqual(0, fsck.returncode, fsck.stderr)

    def test_invalid_workspace_delivery_skips_git_compaction(self):
        runner = _load_runner()
        with TemporaryDirectory() as tmp:
            exp = Path(tmp) / "outputs/group/exp"
            runner.prepare_exp(exp)
            upper = exp / "run/.codex-home/sessions/a/b/c"
            upper.mkdir(parents=True)
            (upper / "rollout-test.jsonl").write_text("{}\n", encoding="utf-8")

            with (
                mock.patch.object(
                    runner,
                    "validate_workspace_delivery",
                    side_effect=runner.PilotError("invalid final delivery"),
                ),
                mock.patch.object(runner, "compact_exp_history") as compact,
            ):
                status = runner.finalize_pilot(exp, 0, {"KEEP_STATE": "1"})

        self.assertEqual(runner.ARTIFACT_CONTRACT_STATUS, status)
        compact.assert_not_called()


class BuildBwrapArgvSeamTests(unittest.TestCase):
    """Focused checks against build_bwrap_argv's browser-related seam.

    These tests bypass most of build_bwrap_argv's environmental prechecks
    by targeting the internal helpers exposed via _load_runner(). They
    intentionally do NOT reproduce the full bwrap argv contract; the pilot
    integration suite covers that end-to-end.
    """

    def test_browser_runtime_contract_imported(self):
        runner = _load_runner()
        self.assertTrue(hasattr(runner, "SANDBOX_MOUNT_ROOT"))
        self.assertTrue(hasattr(runner, "BROWSER_RUNTIME_CONTRACT"))
        self.assertEqual(
            runner.BROWSER_RUNTIME_CONTRACT["sandbox_mount_root"],
            runner.SANDBOX_MOUNT_ROOT,
        )

    def test_render_mcp_config_available_in_runner(self):
        runner = _load_runner()
        self.assertTrue(hasattr(runner, "render_mcp_config"))
        toml = runner.render_mcp_config("http://127.0.0.1:1/mcp")
        self.assertIn("[mcp_servers.browser]", toml)


class NoLegacySymbolsTests(unittest.TestCase):
    """Prevent reintroducing the deleted sealed/gate/broker/surface surface."""

    FORBIDDEN = (
        "browser_sidecar",
        "browser_surface",
        "browser_gate",
        "BrowserSidecarJob",
        "BrowserSurfaceError",
        "NestedGateChannel",
        "prepare_nested_browser_gate",
        "_build_gate_artifact",
        "_gate_surface_manifest",
        "sidecar_receipt_succeeded",
        "BROKER_IMAGE_ID",
        "NESTED_GATE",
        "RECEIPT_SCHEMA",
    )

    def test_runner_py_free_of_legacy_symbols(self):
        text = (PILOT_ROOT / "runner.py").read_text(encoding="utf-8")
        offenders = [name for name in self.FORBIDDEN if name in text]
        self.assertEqual(
            offenders,
            [],
            f"legacy sidecar/broker/gate symbols reappeared in runner.py: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
