"""Fail-closed coverage for the shared plugin-authority module.

These tests exercise the schema, identity binding, atomic pointer swap, and
path-escape rejection guarantees documented in ``plugin_deployment``. They
never touch a real Codex install or a real CVM — the deployment tree is a
fabricated file layout under a temporary root that mimics the shape the
CVM-side publisher produces, using the shared authority fixture to reach the
manifest-recompute code path with real bytes on disk.
"""

from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
from tests.python.support import authority_fixtures


plugin_deployment = authority_fixtures.plugin_deployment
smoke = authority_fixtures.smoke
PluginAuthorityError = plugin_deployment.PluginAuthorityError
DeploymentReceipt = plugin_deployment.DeploymentReceipt


class PortableImportTests(unittest.TestCase):
    def test_module_import_does_not_require_posix_fcntl(self) -> None:
        script = """
import builtins

real_import = builtins.__import__
def portable_import(name, *args, **kwargs):
    if name == "fcntl":
        raise ModuleNotFoundError("No module named 'fcntl'", name="fcntl")
    return real_import(name, *args, **kwargs)

builtins.__import__ = portable_import
import scripts.pilot.plugin_deployment
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_direct_pilot_import_can_recompute_manifest(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        script = """
import tempfile
import sys
from pathlib import Path

import plugin_deployment

with tempfile.TemporaryDirectory() as root_text:
    root = Path(root_text)
    repo_root = str(Path(plugin_deployment.__file__).resolve().parents[2])
    assert repo_root not in sys.path
    (root / "fixture.txt").write_text("fixture", encoding="utf-8")
    digest, count = plugin_deployment._compute_manifest_digest(root)
    assert len(digest) == 64
    assert count == 1
    assert repo_root not in sys.path
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT / "scripts/pilot",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_direct_pilot_import_preserves_nested_module_error(self) -> None:
        script = """
import builtins

import plugin_deployment

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "scripts.release":
        raise ModuleNotFoundError(
            "No module named 'nested_dependency'", name="nested_dependency"
        )
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
try:
    plugin_deployment._load_smoke_installed_plugin()
except ModuleNotFoundError as exc:
    assert exc.name == "nested_dependency"
else:
    raise AssertionError("nested dependency error was swallowed")
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT / "scripts/pilot",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provenance(
    *,
    stage: str | None = None,
    head: str = "0" * 40,
    branch: str = "develop",
    state: str = "clean",
    sent_bytes: int = 1,
) -> dict[str, object]:
    return {
        "schema": "text-to-cad.push-provenance/2",
        "mac_branch": branch,
        "mac_head": head,
        "mac_state": state,
        "stage_manifest_digest": stage or _digest("transferred-stage"),
        "transfer_summary": {"sent_bytes": sent_bytes},
        "runtime_attestation": {
            path: "a" * 64
            for path in plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS
        },
    }


class DeploymentIdentityTests(unittest.TestCase):
    def test_deployment_id_binds_digest_version_and_transferred_stage(self) -> None:
        digest = _digest("prepared-tree")
        provenance = _provenance()
        a = plugin_deployment.compute_deployment_id(digest, "0.4.21", provenance)
        b = plugin_deployment.compute_deployment_id(digest, "0.4.22", provenance)
        c = plugin_deployment.compute_deployment_id(
            digest,
            "0.4.21",
            _provenance(stage=_digest("different-transferred-stage")),
        )
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(
            a,
            plugin_deployment.compute_deployment_id(digest, "0.4.21", provenance),
        )

    def test_deployment_id_binds_git_provenance_but_not_transfer_statistics(self) -> None:
        digest = _digest("prepared-tree")
        baseline = plugin_deployment.compute_deployment_id(
            digest, "0.4.21", _provenance()
        )
        self.assertNotEqual(
            baseline,
            plugin_deployment.compute_deployment_id(
                digest, "0.4.21", _provenance(head="1" * 40)
            ),
        )
        self.assertNotEqual(
            baseline,
            plugin_deployment.compute_deployment_id(
                digest, "0.4.21", _provenance(branch="feature")
            ),
        )
        self.assertNotEqual(
            baseline,
            plugin_deployment.compute_deployment_id(
                digest, "0.4.21", _provenance(state="dirty")
            ),
        )
        self.assertEqual(
            baseline,
            plugin_deployment.compute_deployment_id(
                digest, "0.4.21", _provenance(sent_bytes=999)
            ),
        )

    def test_deployment_id_rejects_invalid_digest(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id("not-hex", "0.4.21", _provenance())
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id("a" * 10, "0.4.21", _provenance())

    def test_deployment_id_rejects_invalid_stage_digest(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id(
                _digest("x"), "0.4.21", _provenance(stage="bad")
            )

    def test_deployment_id_rejects_blank_version(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id(_digest("x"), "  ", _provenance())


class ResolveCurrentAuthorityTests(unittest.TestCase):
    def test_missing_pointer_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            plugin_deployment.ensure_authority_root(root)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_valid_authority_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            observed = plugin_deployment.resolve_current_authority(root)
            self.assertEqual(observed.deployment_id, fixture.receipt.deployment_id)
            skill_dirs = plugin_deployment.resolved_skill_directories(observed)
            self.assertGreater(len(skill_dirs), 0)

    def test_stale_digest_pointer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            document = json.loads(pointer.read_text(encoding="utf-8"))
            document["prepared_manifest_digest"] = _digest("different")
            pointer.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_pointer_disagreeing_with_on_disk_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            document = json.loads(pointer.read_text(encoding="utf-8"))
            document["installed_manifest_digest"] = _digest("tampered")
            pointer.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_unknown_receipt_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            receipt_path = fixture.deployment_dir / plugin_deployment.RECEIPT_FILE
            document = json.loads(receipt_path.read_text(encoding="utf-8"))
            document["unbound"] = True
            receipt_path.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(PluginAuthorityError, "unknown keys"):
                plugin_deployment.resolve_current_authority(root)

    def test_symlink_inside_installed_cache_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            evil = fixture.installed_path / "skills" / "sneaky-link"
            outside = root / "outside-target"
            outside.mkdir()
            os.symlink(outside, evil)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_prepared_tree_tamper_is_caught_by_manifest_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.publish_tree / "unrecorded-addition").write_bytes(b"hostile\n")
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_installed_cache_tamper_is_caught_by_manifest_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.installed_path / "AGENTS.md").write_text(
                "modified\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_installed_cache_mode_tamper_is_caught_by_manifest_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            target = fixture.installed_path / "AGENTS.md"
            target.chmod(0o666)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_installed_directory_mode_tamper_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.installed_path / "skills").chmod(0o777)
            with self.assertRaisesRegex(PluginAuthorityError, "directory mode"):
                plugin_deployment.resolve_current_authority(root)

    def test_rewritten_receipt_digests_cannot_rebind_installed_content(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.installed_path / "AGENTS.md").write_text(
                "attacker\n", encoding="utf-8"
            )
            document = fixture.receipt.as_dict()
            document["installed_manifest_digest"] = smoke.compute_manifest(
                fixture.installed_path
            ).digest
            document["codex_home_manifest_digest"] = smoke.compute_manifest(
                fixture.codex_home,
                private_paths=(plugin_deployment.CONFIG_TOML_NAME,),
            ).digest
            receipt_path = fixture.deployment_dir / plugin_deployment.RECEIPT_FILE
            receipt_path.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            plugin_deployment.pointer_path(root).write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                PluginAuthorityError, "identity-bound publish tree"
            ):
                plugin_deployment.resolve_current_authority(root)

    def test_critical_runtime_removal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            probe_dir = fixture.installed_path / "skills/cad-viewer/scripts/viewer"
            (probe_dir / "package.json").unlink()
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_pointer_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            target = pointer.parent / "pointer-target.json"
            pointer.rename(target)
            os.symlink(target, pointer)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_receipt_file_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            receipt_path = fixture.deployment_dir / plugin_deployment.RECEIPT_FILE
            duplicate = fixture.deployment_dir / "receipt-real.json"
            receipt_path.rename(duplicate)
            os.symlink(duplicate, receipt_path)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_publish_tree_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            renamed = fixture.deployment_dir / "publish-tree-real"
            fixture.publish_tree.rename(renamed)
            os.symlink(renamed, fixture.publish_tree)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_codex_home_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            renamed = fixture.deployment_dir / "codex-home-real"
            fixture.codex_home.rename(renamed)
            os.symlink(renamed, fixture.codex_home)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_installed_path_pointer_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            document = json.loads(pointer.read_text(encoding="utf-8"))
            document["installed_path"] = str(root / "somewhere-else")
            pointer.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_authority_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            real = plugin_deployment.authority_root(root)
            relocated = root / ".text-to-cad-codex-real"
            real.rename(relocated)
            os.symlink(relocated, real)
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_invalid_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            document = fixture.receipt.as_dict()
            document["schema"] = "other/1"
            pointer.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_invalid_transfer_provenance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            document = json.loads(pointer.read_text(encoding="utf-8"))
            document["transfer_provenance"] = {"schema": "unrelated/1"}
            pointer.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)


class PublishAuthorityTests(unittest.TestCase):
    def test_publish_writes_pointer_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            self.assertTrue(pointer.is_file())
            self.assertFalse(pointer.is_symlink())
            document = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(document["deployment_id"], fixture.receipt.deployment_id)

    def test_publish_is_idempotent_for_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            first_mtime = pointer.stat().st_mtime_ns
            plugin_deployment.publish_authority(
                fixture.receipt, codex_home_root=root
            )
            self.assertEqual(pointer.stat().st_mtime_ns, first_mtime)

    def test_publish_rejects_mismatch_between_memory_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            on_disk = fixture.deployment_dir / plugin_deployment.RECEIPT_FILE
            document = json.loads(on_disk.read_text(encoding="utf-8"))
            document["source_git_sha"] = "cafebabe"
            on_disk.write_text(
                json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.publish_authority(
                    fixture.receipt, codex_home_root=root
                )

    def test_publish_requires_deployment_receipt_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.deployment_dir / plugin_deployment.RECEIPT_FILE).unlink()
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.publish_authority(
                    fixture.receipt, codex_home_root=root
                )


def _serialized_publish_worker(root_text: str, dedupe_token: str) -> None:
    root = Path(root_text)
    fixture = authority_fixtures.build_authority(
        root, dedupe_token=dedupe_token, publish=False
    )
    time.sleep(0.05)
    plugin_deployment.publish_authority(fixture.receipt, codex_home_root=root)


def _concurrent_move_worker(root_text: str, dedupe_token: str) -> int:
    # This helper is a low-level probe: two workers race to create the same
    # deployment slot. The one that wins publishes; the loser must fail
    # closed. build_authority itself uses move_into_place which is exactly
    # what the review demands be race-safe under a same-content collision.
    from scripts.pilot import plugin_deployment as pd  # local import for fork worker

    root = Path(root_text)
    try:
        authority_fixtures.build_authority(root, dedupe_token=dedupe_token)
    except pd.PluginAuthorityError:
        return 1
    return 0


class ConcurrentPublishTests(unittest.TestCase):
    def test_concurrent_publications_do_not_corrupt_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            plugin_deployment.ensure_authority_root(root)
            ctx = multiprocessing.get_context("fork")
            procs = [
                ctx.Process(
                    target=_serialized_publish_worker,
                    args=(root_text, f"conc-{i}"),
                )
                for i in range(4)
            ]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(timeout=60)
                self.assertEqual(proc.exitcode, 0)
            observed = plugin_deployment.resolve_current_authority(root)
            self.assertTrue(observed.deployment_id)


class MoveIntoPlaceTests(unittest.TestCase):
    def test_move_into_place_rejects_existing_slot(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            staging = root / "staging"
            staging.mkdir()
            target = root / "target"
            target.mkdir()
            (target / "keep").write_text("x", encoding="utf-8")
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.move_into_place(staging, target)
            self.assertTrue((target / "keep").is_file())


class ResolvedSkillDirectoriesTests(unittest.TestCase):
    def test_unregistered_addition_below_installed_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            # An unrecorded file added after publication must be caught by the
            # manifest recompute — silently tolerating the addition would be
            # exactly the P1-2 finding.
            (fixture.installed_path / "skills" / "no-manifest").mkdir()
            (fixture.installed_path / "skills" / "no-manifest" / "planted.txt").write_text(
                "unrecorded\n", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.resolve_current_authority(root)

    def test_missing_skills_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            import shutil

            shutil.rmtree(fixture.installed_path / "skills")
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.installed_skills_root(fixture.receipt)


class MaterializeJobCodexHomeTests(unittest.TestCase):
    def test_materialization_rewrites_marketplace_source_and_merges_extra(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            target = root / "job-home"
            plugin_deployment.materialize_job_codex_home(
                fixture.receipt,
                target,
                extra_toml='[mcp_servers.browser]\nurl = "http://x"\n',
            )
            raw = (target / "config.toml").read_text(encoding="utf-8")
            # Parse structurally: this catches the exact regression where the
            # rewriter used to collapse ``source`` and its sibling
            # ``source_type`` into two ``source = ...`` lines and Codex 0.147
            # rejected the whole CODEX_HOME with ``duplicate key``.
            parsed = tomllib.loads(raw)
            marketplace = parsed["marketplaces"]["text-to-cad"]
            self.assertEqual(marketplace["source"], "/opt/text-to-cad-publish-tree")
            self.assertEqual(
                marketplace["source_type"],
                "local",
                "source_type sibling key must survive the source rewrite",
            )
            plugin_entry = parsed["plugins"]["cad@text-to-cad"]
            self.assertIs(plugin_entry["enabled"], True)
            self.assertEqual(parsed["mcp_servers"]["browser"]["url"], "http://x")
            # And the byte-level shape: ``source_type = "local"`` must be
            # unchanged from the fixture's authority config.toml.
            self.assertIn('source_type = "local"', raw)

    def test_materialization_rejects_extra_touching_registration(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            target = root / "job-home"
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.materialize_job_codex_home(
                    fixture.receipt,
                    target,
                    extra_toml='[plugins."cad@text-to-cad"]\nenabled = false\n',
                )
            self.assertFalse(target.exists())

    def test_materialization_verifies_installed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            # Tamper the authority *after* it was published so the receipt no
            # longer matches the bytes on disk; a manifest recompute in the
            # materialize helper must fail closed rather than deliver a
            # corrupted job home.
            (fixture.installed_path / "AGENTS.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            target = root / "job-home"
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.materialize_job_codex_home(
                    fixture.receipt, target
                )
            self.assertFalse(target.exists())

    def test_none_sandbox_source_leaves_marketplace_source_unrewritten(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            target = root / "job-home"
            plugin_deployment.materialize_job_codex_home(
                fixture.receipt, target, sandbox_marketplace_source=None
            )
            config = (target / "config.toml").read_text(encoding="utf-8")
            # Original authority publish tree path preserved when caller opts out.
            self.assertIn(str(fixture.publish_tree), config)


class MarketplaceSourceRewriteTests(unittest.TestCase):
    """Direct coverage for ``_rewrite_marketplace_source``.

    Regression: previously matched via ``stripped.startswith("source")``, which
    also matched sibling keys like ``source_type = "local"`` that Codex 0.147
    writes into the same section. Two lines then began with ``source = ...``
    and ``codex plugin list --json`` refused the CODEX_HOME with
    ``config.toml: duplicate key``. The rewriter must now match only the exact
    TOML key ``source`` and preserve every other ``source*`` key byte-for-byte.
    """

    _REAL_CODEX_CONFIG = (
        "[marketplaces.text-to-cad]\n"
        'source = "/orig/publish-tree"\n'
        'source_type = "local"\n'
        '[plugins."cad@text-to-cad"]\n'
        "enabled = true\n"
    )

    def test_source_type_sibling_survives_rewrite_byte_for_byte(self) -> None:
        rewritten = plugin_deployment._rewrite_marketplace_source(
            self._REAL_CODEX_CONFIG, "/opt/text-to-cad-publish-tree"
        )
        # The literal ``source_type`` line must be present unchanged.
        self.assertIn('source_type = "local"\n', rewritten)
        # Exactly one ``source = ...`` line must exist.
        source_lines = [
            line
            for line in rewritten.splitlines()
            if line.strip().startswith("source ") or line.strip().startswith("source=")
        ]
        self.assertEqual(
            len(source_lines),
            1,
            f"expected exactly one source = line, got {source_lines!r}",
        )

    def test_rewritten_config_parses_and_registers_plugin(self) -> None:
        rewritten = plugin_deployment._rewrite_marketplace_source(
            self._REAL_CODEX_CONFIG, "/opt/text-to-cad-publish-tree"
        )
        parsed = tomllib.loads(rewritten)
        marketplace = parsed["marketplaces"]["text-to-cad"]
        self.assertEqual(marketplace["source"], "/opt/text-to-cad-publish-tree")
        self.assertEqual(marketplace["source_type"], "local")
        self.assertIs(parsed["plugins"]["cad@text-to-cad"]["enabled"], True)

    def test_variant_whitespace_and_no_quotes_source_key_matches(self) -> None:
        variants = (
            ("source=\"/x\"\n", 'source = "/opt/publish"\n'),
            ("  source   =   \"/x\"\n", 'source = "/opt/publish"\n'),
            ("source\t=\t\"/x\"\n", 'source = "/opt/publish"\n'),
        )
        for original_line, expected_line in variants:
            config = (
                "[marketplaces.text-to-cad]\n"
                + original_line
                + 'source_type = "local"\n'
            )
            rewritten = plugin_deployment._rewrite_marketplace_source(
                config, "/opt/publish"
            )
            self.assertIn(expected_line, rewritten, f"variant {original_line!r} not rewritten")
            self.assertIn('source_type = "local"\n', rewritten)
            # And the rewrite must still yield a parseable file.
            tomllib.loads(rewritten)

    def test_duplicate_exact_source_keys_fail_closed(self) -> None:
        # A configuration with two literal ``source = ...`` lines is
        # unrecoverable: rewriting either alone would leave the other as a
        # divergent authority pointer. The helper must refuse rather than
        # write silently.
        broken_config = (
            "[marketplaces.text-to-cad]\n"
            'source = "/one"\n'
            'source = "/two"\n'
            '[plugins."cad@text-to-cad"]\n'
            "enabled = true\n"
        )
        with self.assertRaises(PluginAuthorityError) as ctx:
            plugin_deployment._rewrite_marketplace_source(
                broken_config, "/opt/publish"
            )
        self.assertIn("multiple", str(ctx.exception).lower())

    def test_zero_source_keys_fail_closed(self) -> None:
        # Section present but ``source =`` missing entirely — refuse rather
        # than fabricate a source line, which would misrepresent the on-disk
        # authority to Codex.
        no_source_config = (
            "[marketplaces.text-to-cad]\n"
            'source_type = "local"\n'
            '[plugins."cad@text-to-cad"]\n'
            "enabled = true\n"
        )
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._rewrite_marketplace_source(
                no_source_config, "/opt/publish"
            )

    def test_source_prefixed_keys_in_unrelated_section_are_untouched(self) -> None:
        # Only assignments inside [marketplaces.text-to-cad] are rewritten;
        # a rogue ``source = ...`` in another section must survive untouched
        # so we cannot corrupt unrelated Codex state.
        config = (
            '[marketplaces.other]\n'
            'source = "/other/tree"\n'
            'source_type = "git"\n'
            "[marketplaces.text-to-cad]\n"
            'source = "/orig"\n'
            'source_type = "local"\n'
        )
        rewritten = plugin_deployment._rewrite_marketplace_source(
            config, "/opt/publish"
        )
        parsed = tomllib.loads(rewritten)
        self.assertEqual(parsed["marketplaces"]["other"]["source"], "/other/tree")
        self.assertEqual(parsed["marketplaces"]["other"]["source_type"], "git")
        self.assertEqual(
            parsed["marketplaces"]["text-to-cad"]["source"], "/opt/publish"
        )
        self.assertEqual(
            parsed["marketplaces"]["text-to-cad"]["source_type"], "local"
        )


class ConfigTomlBindingTests(unittest.TestCase):
    """Regression: whole CODEX_HOME (config.toml included) is receipt-bound.

    Pre-fix the receipt hashed only the installed-plugin cache subtree, so
    a post-publication edit that flipped ``enabled = true`` → ``enabled =
    false`` or redirected the marketplace ``source`` to attacker-controlled
    state survived :func:`resolve_current_authority` untouched. These tests
    exercise the whole-home manifest and parsed-registration invariants.
    """

    def _resolve(self, root: Path):
        return plugin_deployment.resolve_current_authority(root)

    def test_authority_binds_config_toml_digest(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            self.assertTrue(fixture.receipt.codex_home_manifest_digest)
            observed = self._resolve(root)
            self.assertEqual(
                observed.codex_home_manifest_digest,
                fixture.receipt.codex_home_manifest_digest,
            )

    def test_flipping_enabled_false_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            config_path = fixture.codex_home / "config.toml"
            tampered = config_path.read_text(encoding="utf-8").replace(
                "enabled = true", "enabled = false"
            )
            config_path.write_text(tampered, encoding="utf-8")
            with self.assertRaises(PluginAuthorityError):
                self._resolve(root)

    def test_marketplace_source_redirect_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            config_path = fixture.codex_home / "config.toml"
            original = config_path.read_text(encoding="utf-8")
            redirected = original.replace(
                str(fixture.publish_tree), "/attacker/controlled/tree"
            )
            self.assertNotEqual(original, redirected)
            config_path.write_text(redirected, encoding="utf-8")
            with self.assertRaises(PluginAuthorityError):
                self._resolve(root)

    def test_config_toml_deletion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            (fixture.codex_home / "config.toml").unlink()
            with self.assertRaises(PluginAuthorityError):
                self._resolve(root)

class AuthorityRootSymlinkGuardTests(unittest.TestCase):
    """Regression: ``ensure_authority_root`` must not follow a symlinked root.

    Prior to the fix, ``mkdir(parents=True, exist_ok=True)`` happily
    succeeded when ``~/.text-to-cad-codex`` was already a symlink to any
    directory the attacker chose; every subsequent write in the subtree
    (deployments/, lock, pointer, deployment slots) then landed outside
    the trusted host home. The guards use ``os.lstat`` so a pre-existing
    symlink at the leaf is rejected before any ``mkdir`` call.
    """

    def test_authority_root_symlink_is_rejected_before_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            target = root / "attacker-controlled"
            target.mkdir()
            os.symlink(target, plugin_deployment.authority_root(root))
            with self.assertRaisesRegex(PluginAuthorityError, "authority root"):
                plugin_deployment.ensure_authority_root(root)
            # And no side effects: nothing was written into the symlink target.
            self.assertEqual(list(target.iterdir()), [])

    def test_deployments_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            authority = plugin_deployment.authority_root(root)
            authority.mkdir()
            target = root / "attacker-deployments"
            target.mkdir()
            os.symlink(target, authority / plugin_deployment.DEPLOYMENTS_DIRNAME)
            with self.assertRaisesRegex(
                PluginAuthorityError, "deployments directory"
            ):
                plugin_deployment.ensure_authority_root(root)
            self.assertEqual(list(target.iterdir()), [])

    def test_publish_lock_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            lock = plugin_deployment.lock_path(root)
            if lock.exists():
                lock.unlink()
            attacker = root / "attacker-log"
            attacker.write_text("victim\n", encoding="utf-8")
            os.symlink(attacker, lock)
            with self.assertRaisesRegex(PluginAuthorityError, "publish lock"):
                plugin_deployment.publish_authority(
                    fixture.receipt, codex_home_root=root
                )
            # The attacker-controlled file must be untouched.
            self.assertEqual(
                attacker.read_text(encoding="utf-8"), "victim\n"
            )

    def test_held_publish_lock_detects_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            plugin_deployment.ensure_authority_root(root)
            lock = plugin_deployment.lock_path(root)
            with plugin_deployment.publication_lock(lock) as verify_lock:
                lock.unlink()
                lock.write_text("replacement\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    PluginAuthorityError, "changed while held"
                ):
                    verify_lock()

    def test_pointer_symlink_is_rejected_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root)
            pointer = plugin_deployment.pointer_path(root)
            pointer.unlink()
            attacker = root / "attacker-pointer.json"
            attacker.write_text("victim\n", encoding="utf-8")
            os.symlink(attacker, pointer)
            with self.assertRaisesRegex(PluginAuthorityError, "authority pointer"):
                plugin_deployment.publish_authority(
                    fixture.receipt, codex_home_root=root
                )
            self.assertEqual(
                attacker.read_text(encoding="utf-8"), "victim\n"
            )

    def test_pointer_temporary_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fixture = authority_fixtures.build_authority(root, publish=False)
            pointer = plugin_deployment.pointer_path(root)
            temporary = pointer.with_name(f".{pointer.name}.tmp")
            attacker = root / "attacker-pointer.json"
            attacker.write_text("victim\n", encoding="utf-8")
            os.symlink(attacker, temporary)

            plugin_deployment.publish_authority(
                fixture.receipt, codex_home_root=root
            )

            self.assertEqual(
                attacker.read_text(encoding="utf-8"), "victim\n"
            )
            self.assertTrue(temporary.is_symlink())
            self.assertTrue(pointer.is_file())


class TransferProvenanceValidationTests(unittest.TestCase):
    def test_valid_document_round_trips(self) -> None:
        document = {
            "schema": "text-to-cad.push-provenance/2",
            "mac_branch": "develop",
            "mac_head": "0" * 40,
            "mac_state": "clean",
            "stage_manifest_digest": "c" * 64,
            "transfer_summary": {"sent_bytes": 1, "received_bytes": 2, "bytes_per_second": 3.0},
            "runtime_attestation": {
                path: "a" * 64
                for path in plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS
            },
        }
        self.assertEqual(
            plugin_deployment._validate_transfer_provenance(document)["mac_branch"],
            "develop",
        )

    def test_wrong_schema_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "unrelated/1", "mac_branch": "x", "mac_head": "0" * 40,
                 "mac_state": "clean", "stage_manifest_digest": "c" * 64}
            )

    def test_dirty_head_shape_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "text-to-cad.push-provenance/2", "mac_branch": "develop",
                 "mac_head": "not-a-sha", "mac_state": "clean",
                 "stage_manifest_digest": "c" * 64}
            )

    def test_missing_state_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "text-to-cad.push-provenance/2", "mac_branch": "develop",
                 "mac_head": "0" * 40, "mac_state": "unknown",
                 "stage_manifest_digest": "c" * 64}
            )

    def test_missing_or_empty_runtime_attestation_is_rejected(self) -> None:
        for runtime in (None, {}):
            document = _provenance()
            if runtime is None:
                del document["runtime_attestation"]
            else:
                document["runtime_attestation"] = runtime
            with self.subTest(runtime=runtime):
                with self.assertRaisesRegex(
                    PluginAuthorityError, "runtime_attestation"
                ):
                    plugin_deployment._validate_transfer_provenance(document)

    def test_missing_stage_manifest_digest_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError) as ctx:
            plugin_deployment._validate_transfer_provenance(
                {"schema": "text-to-cad.push-provenance/2", "mac_branch": "develop",
                 "mac_head": "0" * 40, "mac_state": "clean"}
            )
        self.assertIn("stage_manifest_digest", str(ctx.exception))

    def test_malformed_stage_manifest_digest_is_rejected(self) -> None:
        for bad in ("", "not-hex", "c" * 63, "C" * 64, 0):
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment._validate_transfer_provenance(
                    {"schema": "text-to-cad.push-provenance/2", "mac_branch": "develop",
                     "mac_head": "0" * 40, "mac_state": "clean",
                     "stage_manifest_digest": bad}
                )


class StageManifestTests(unittest.TestCase):
    """Cover the canonical stage manifest writer + materializer.

    The manifest binds the exact set of regular files the Mac stage carried
    at push time; the CVM publisher materializes publish-tree-src from
    exactly those paths, so anything the persistent ``~/text-to-cad`` overlay
    has accumulated (stale tracked files that were later deleted from the
    Mac side, symlinks, ad-hoc scratch) cannot enter deployment identity.
    """

    def _seed_stage(self, stage: Path) -> None:
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "VERSION").write_text("0.4.21\n", encoding="utf-8")
        (stage / "AGENTS.md").write_text("agents\n", encoding="utf-8")
        nested = stage / "skills" / "cad" / "SKILL.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("cad\n", encoding="utf-8")

    def test_write_stage_manifest_excludes_itself(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            stage = Path(root_text) / "stage"
            self._seed_stage(stage)
            digest = plugin_deployment.write_stage_manifest(stage)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            manifest_path = stage / plugin_deployment.STAGE_MANIFEST_FILENAME
            self.assertTrue(manifest_path.is_file())
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                document["schema"], plugin_deployment.STAGE_MANIFEST_SCHEMA
            )
            listed = {entry["path"] for entry in document["entries"]}
            self.assertIn("VERSION", listed)
            self.assertIn("AGENTS.md", listed)
            self.assertIn("skills/cad/SKILL.md", listed)
            self.assertNotIn(
                plugin_deployment.STAGE_MANIFEST_FILENAME, listed
            )

    def test_write_stage_manifest_rejects_unmanifested_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            stage = Path(root_text) / "stage"
            self._seed_stage(stage)
            bin_dir = stage / "viewer" / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            os.symlink("../tool/index.js", bin_dir / "tool")

            with self.assertRaisesRegex(
                PluginAuthorityError, "unmanifested symlink"
            ):
                plugin_deployment.write_stage_manifest(stage)

    def test_materialize_rejects_symlinked_manifest_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            manifest = src / plugin_deployment.STAGE_MANIFEST_FILENAME
            outside = root / "outside-manifest.json"
            manifest.rename(outside)
            os.symlink(outside, manifest)
            with self.assertRaisesRegex(PluginAuthorityError, "symlink"):
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )

    def test_materialize_copies_only_listed_files_and_drops_stale(self) -> None:
        # Regression: an ordinary tracked file that lingers in the persistent
        # remote-shaped source but is NOT in the manifest must be absent from
        # the publish staging, while every listed file is copied and hash
        # checked.
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "text-to-cad"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            # Now simulate a stale file appearing in the persistent overlay
            # AFTER the manifest was authored (e.g. a prior push wrote it and
            # a later push removed it from the Mac stage but ~/text-to-cad
            # keeps it because rsync ran without --delete).
            stale = src / "scripts" / "leftover.py"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text("stale\n", encoding="utf-8")
            (src / "unrelated.txt").write_text("stray\n", encoding="utf-8")

            dst = root / "publish-src"
            plugin_deployment.materialize_from_stage_manifest(
                src, dst, expected_manifest_digest=digest
            )

            self.assertTrue((dst / "VERSION").is_file())
            self.assertTrue((dst / "AGENTS.md").is_file())
            self.assertTrue((dst / "skills" / "cad" / "SKILL.md").is_file())
            # Stale files absent.
            self.assertFalse((dst / "scripts" / "leftover.py").exists())
            self.assertFalse((dst / "unrelated.txt").exists())
            # Manifest file itself must never enter publish staging.
            self.assertFalse(
                (dst / plugin_deployment.STAGE_MANIFEST_FILENAME).exists()
            )

    def test_materialize_rejects_symlink_source(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            # Replace a listed regular file with a symlink after the manifest
            # was authored — the materializer must refuse.
            target = src / "VERSION"
            target.unlink()
            os.symlink(root / "elsewhere", target)
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )
            self.assertIn("symlink", str(ctx.exception).lower())

    def test_materialize_rejects_symlink_source_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            real_skills = root / "real-skills"
            (real_skills / "cad").mkdir(parents=True)
            (real_skills / "cad" / "SKILL.md").write_text(
                "cad\n", encoding="utf-8"
            )
            shutil.rmtree(src / "skills")
            os.symlink(real_skills, src / "skills")

            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )
            self.assertIn("ancestor is a symlink", str(ctx.exception).lower())

    def test_materialize_rejects_content_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            (src / "VERSION").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )
            self.assertIn("content mismatch", str(ctx.exception).lower())

    def test_materialize_rejects_mode_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            target = src / "VERSION"
            target.chmod(0o755)
            digest = plugin_deployment.write_stage_manifest(src)
            target.chmod(0o644)
            with self.assertRaisesRegex(PluginAuthorityError, "mode mismatch"):
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )

    def test_materialize_rejects_missing_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            digest = plugin_deployment.write_stage_manifest(src)
            (src / "VERSION").unlink()
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst", expected_manifest_digest=digest
                )
            self.assertIn("missing", str(ctx.exception).lower())

    def test_materialize_rejects_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            # No manifest written.
            with self.assertRaises(PluginAuthorityError):
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst",
                    expected_manifest_digest="c" * 64,
                )

    def test_materialize_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            self._seed_stage(src)
            plugin_deployment.write_stage_manifest(src)
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst",
                    expected_manifest_digest="d" * 64,
                )
            self.assertIn("digest", str(ctx.exception).lower())

    def test_materialize_rejects_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            src.mkdir()
            (src / plugin_deployment.STAGE_MANIFEST_FILENAME).write_text(
                "{not-json", encoding="utf-8"
            )
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst",
                    expected_manifest_digest="e" * 64,
                )
            self.assertIn("json", str(ctx.exception).lower())

    def test_materialize_rejects_absolute_path_entry(self) -> None:
        self._reject_tampered_manifest(
            [{"path": "/etc/passwd", "sha256": "a" * 64, "mode": "0644"}],
            "absolute",
        )

    def test_materialize_rejects_traversal_entry(self) -> None:
        self._reject_tampered_manifest(
            [{"path": "../outside", "sha256": "a" * 64, "mode": "0644"}],
            "traversal",
        )

    def test_materialize_rejects_duplicate_entry(self) -> None:
        self._reject_tampered_manifest(
            [
                {"path": "VERSION", "sha256": "a" * 64, "mode": "0644"},
                {"path": "VERSION", "sha256": "a" * 64, "mode": "0644"},
            ],
            "duplicate",
        )

    def test_materialize_rejects_manifest_listing_itself(self) -> None:
        self._reject_tampered_manifest(
            [{
                "path": plugin_deployment.STAGE_MANIFEST_FILENAME,
                "sha256": "a" * 64,
                "mode": "0644",
            }],
            "may not list itself",
        )

    def _reject_tampered_manifest(
        self, entries: list[dict[str, str]], expected_substring: str
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src = root / "src"
            src.mkdir()
            document = {
                "schema": plugin_deployment.STAGE_MANIFEST_SCHEMA,
                "entries": entries,
            }
            (src / plugin_deployment.STAGE_MANIFEST_FILENAME).write_text(
                json.dumps(document), encoding="utf-8"
            )
            # Any 64-hex digest: the manifest shape must be rejected before
            # the digest recompute even runs.
            with self.assertRaises(PluginAuthorityError) as ctx:
                plugin_deployment.materialize_from_stage_manifest(
                    src, root / "dst",
                    expected_manifest_digest="0" * 64,
                )
            self.assertIn(expected_substring, str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
