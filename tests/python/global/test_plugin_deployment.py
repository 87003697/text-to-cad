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


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeploymentIdentityTests(unittest.TestCase):
    def test_deployment_id_binds_digest_and_version(self) -> None:
        digest = _digest("prepared-tree")
        a = plugin_deployment.compute_deployment_id(digest, "0.4.21")
        b = plugin_deployment.compute_deployment_id(digest, "0.4.22")
        self.assertNotEqual(a, b)
        self.assertEqual(a, plugin_deployment.compute_deployment_id(digest, "0.4.21"))

    def test_deployment_id_rejects_invalid_digest(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id("not-hex", "0.4.21")
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id("a" * 10, "0.4.21")

    def test_deployment_id_rejects_blank_version(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment.compute_deployment_id(_digest("x"), "  ")


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


class TransferProvenanceValidationTests(unittest.TestCase):
    def test_valid_document_round_trips(self) -> None:
        document = {
            "schema": "text-to-cad.push-provenance/1",
            "mac_branch": "develop",
            "mac_head": "0" * 40,
            "mac_state": "clean",
            "transfer_summary": {"sent_bytes": 1, "received_bytes": 2, "bytes_per_second": 3.0},
            "runtime_attestation": {"scripts/pilot/runner.py": "a" * 64},
        }
        self.assertEqual(
            plugin_deployment._validate_transfer_provenance(document)["mac_branch"],
            "develop",
        )

    def test_wrong_schema_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "unrelated/1", "mac_branch": "x", "mac_head": "0" * 40,
                 "mac_state": "clean"}
            )

    def test_dirty_head_shape_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "text-to-cad.push-provenance/1", "mac_branch": "develop",
                 "mac_head": "not-a-sha", "mac_state": "clean"}
            )

    def test_missing_state_is_rejected(self) -> None:
        with self.assertRaises(PluginAuthorityError):
            plugin_deployment._validate_transfer_provenance(
                {"schema": "text-to-cad.push-provenance/1", "mac_branch": "develop",
                 "mac_head": "0" * 40, "mac_state": "unknown"}
            )


if __name__ == "__main__":
    unittest.main()
