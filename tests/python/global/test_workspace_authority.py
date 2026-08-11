from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
AUTHORITY = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-authority"
INSTALLED_AUTHORITY = (
    REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-authority/__main__.py"
)
GENERATED_AUTHORITY = (
    REPO_ROOT / ".claude/skills/pilot-review/scripts/workspace_authority.py"
)


def load_authority_module():
    spec = importlib.util.spec_from_file_location(
        "workspace_authority", INSTALLED_AUTHORITY
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkspaceAuthorityProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="issue32-authority-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run_git("init", "--quiet")
        self.run_git("config", "user.name", "authority-test")
        self.run_git("config", "user.email", "authority-test@localhost")
        self.write_json(
            self.workspace / "workspace.json",
            {"schema": "mesh-to-cad.workspace/1", "workspace_id": "portable-test"},
        )
        (self.workspace / ".gitignore").write_text(
            "artifact_manifest.json\nworkspace-authority.bundle\nworkspace-authority.json\n",
            encoding="utf-8",
        )
        self.run_git("add", ".gitignore", "workspace.json")
        self.run_git("commit", "--quiet", "-m", "workspace: initialize")
        self.write_json(
            self.workspace / "final/manifest.json",
            {"schema": "mesh-to-cad.final-delivery/1", "identity_sha256": "a" * 64},
        )
        self.run_git("add", "final/manifest.json")
        self.run_git("commit", "--quiet", "-m", "workspace: publish final")
        self.validator = self.root / "workspace-validator.py"
        self.validator.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, subprocess, sys\n"
            "root = pathlib.Path(sys.argv[sys.argv.index('--workspace') + 1])\n"
            "head = subprocess.run(['git','rev-parse','HEAD'],cwd=root,check=True,capture_output=True,text=True).stdout.strip()\n"
            "payload = {'ok': True, 'valid': True, 'graph': {'schema': 'mesh-to-cad.step-index/1', 'final_delivery': {'identity_sha256': '" + "a" * 64 + "'}}, 'recovery': [], 'head': head}\n"
            "print(json.dumps(payload, separators=(',', ':')))\n",
            encoding="utf-8",
        )
        self.validator.chmod(0o755)

    def run_git(self, *argv: str) -> str:
        return subprocess.run(
            ["git", *argv],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")

    def run_authority(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(AUTHORITY), *argv],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def create_package(self) -> dict:
        created = self.run_authority(
            "create",
            "--workspace",
            str(self.workspace),
            "--workspace-helper",
            str(self.validator),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        return json.loads(created.stdout)

    def pulled_copy(self) -> Path:
        pulled = self.root / f"pulled-{len(list(self.root.glob('pulled-*')))}"
        shutil.copytree(self.workspace, pulled, ignore=shutil.ignore_patterns(".git"))
        return pulled

    def audit(
        self,
        pulled: Path,
        *,
        timeout: str = "10",
        expected_authority: list[dict[str, object]] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            "audit",
            "--source",
            str(pulled),
            "--workspace-helper",
            str(self.validator),
            "--timeout-seconds",
            timeout,
        ]
        if expected_authority is not None:
            argv.extend(
                ["--expected-authority-json", json.dumps(expected_authority)]
            )
        return self.run_authority(*argv)

    def test_create_and_audit_portable_authority_through_process_interface(self) -> None:
        create_payload = self.create_package()
        self.assertTrue(create_payload["ok"])

        receipt_path = self.workspace / "workspace-authority.json"
        bundle_path = self.workspace / "workspace-authority.bundle"
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        self.assertEqual(
            receipt_bytes,
            (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        self.assertEqual(receipt["schema"], "mesh-to-cad.workspace-authority/1")
        self.assertEqual(receipt["workspace"]["publication_ref"], "refs/workspace-authority/portable-v1")
        self.assertEqual(receipt["workspace"]["head"], self.run_git("rev-parse", "HEAD"))
        self.assertEqual(len(receipt["required_commits"]), 2)
        self.assertEqual(receipt["required_commits"][0]["parents"], [])
        self.assertEqual(
            receipt["required_commits"][1]["parents"],
            [receipt["required_commits"][0]["commit"]],
        )
        self.assertTrue(bundle_path.is_file())
        heads = subprocess.run(
            ["git", "bundle", "list-heads", str(bundle_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertEqual(
            heads,
            [f"{receipt['workspace']['head']} {receipt['workspace']['publication_ref']}"],
        )

        pulled = self.pulled_copy()
        audited = self.audit(pulled)
        self.assertEqual(audited.returncode, 0, audited.stderr)
        audit_payload = json.loads(audited.stdout)
        self.assertTrue(audit_payload["ok"])
        self.assertEqual(audit_payload["authority"]["mode"], "materialized")
        self.assertEqual(
            audit_payload["authority"]["evidence"],
            ["workspace-authority.json", "workspace-authority.bundle"],
        )
        self.assertEqual(audit_payload["workspace_validation"]["head"], receipt["workspace"]["head"])

    def test_installed_authority_is_self_contained_and_generated_from_canonical_source(self) -> None:
        self.assertTrue(INSTALLED_AUTHORITY.is_file())
        completed = subprocess.run(
            [sys.executable, str(INSTALLED_AUTHORITY), "--help"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(INSTALLED_AUTHORITY.read_bytes(), GENERATED_AUTHORITY.read_bytes())
        reference = (
            REPO_ROOT
            / "skills/mesh-to-cad/references/portable-workspace-authority.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("scripts/pilot/workspace_authority.py", reference)
        self.assertIn(
            "$MESH_TO_CAD_SKILL/scripts/mesh-to-cad-authority", reference
        )

    def test_missing_legacy_authority_is_not_auditable(self) -> None:
        pulled = self.pulled_copy()
        audited = self.audit(pulled)
        payload = json.loads(audited.stdout)
        self.assertEqual(audited.returncode, 2)
        self.assertEqual(payload["classification"], "not_auditable")
        self.assertEqual(payload["authority"]["classification"], "authority_missing")

    def test_digest_mismatch_is_not_auditable(self) -> None:
        self.create_package()
        pulled = self.pulled_copy()
        with (pulled / "workspace-authority.bundle").open("ab") as stream:
            stream.write(b"corrupt")
        payload = json.loads(self.audit(pulled).stdout)
        self.assertEqual(payload["authority"]["classification"], "authority_digest_mismatch")

    def test_expected_transfer_identity_rejects_stale_same_count_authority(self) -> None:
        self.create_package()
        expected = []
        for name in ("workspace-authority.bundle", "workspace-authority.json"):
            data = (self.workspace / name).read_bytes()
            expected.append(
                {
                    "path": name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        pulled = self.pulled_copy()
        receipt = pulled / "workspace-authority.json"
        original = receipt.read_bytes()
        receipt.write_bytes(bytes([original[0] ^ 1]) + original[1:])

        payload = json.loads(
            self.audit(pulled, expected_authority=expected).stdout
        )

        self.assertEqual(
            payload["authority"]["classification"],
            "authority_mount_identity_mismatch",
        )

    def test_unknown_nested_receipt_field_is_rejected(self) -> None:
        self.create_package()
        pulled = self.pulled_copy()
        receipt_path = pulled / "workspace-authority.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["created_by"]["future"] = True
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        payload = json.loads(self.audit(pulled).stdout)
        self.assertEqual(payload["authority"]["classification"], "authority_corrupt_receipt")

    def test_truncated_bundle_with_matching_transport_digest_is_invalid(self) -> None:
        self.create_package()
        pulled = self.pulled_copy()
        bundle_path = pulled / "workspace-authority.bundle"
        truncated = bundle_path.read_bytes()[:64]
        bundle_path.write_bytes(truncated)
        receipt_path = pulled / "workspace-authority.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["bundle"]["size_bytes"] = len(truncated)
        receipt["bundle"]["sha256"] = hashlib.sha256(truncated).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        payload = json.loads(self.audit(pulled).stdout)
        self.assertEqual(payload["authority"]["classification"], "authority_invalid_bundle")

    def test_wrong_ref_and_parent_are_distinct_not_auditable_classes(self) -> None:
        self.create_package()
        for field, value, expected in (
            ("publication_ref", "refs/heads/unrelated", "authority_wrong_ref"),
            ("parents", ["f" * 40], "authority_parent_mismatch"),
        ):
            pulled = self.pulled_copy()
            receipt_path = pulled / "workspace-authority.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if field == "parents":
                receipt["required_commits"][-1][field] = value
            else:
                receipt["workspace"][field] = value
            receipt_path.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.subTest(field=field):
                payload = json.loads(self.audit(pulled).stdout)
                self.assertEqual(payload["authority"]["classification"], expected)

    def test_partial_and_dirty_artifacts_are_distinct_not_auditable_classes(self) -> None:
        self.create_package()
        for mutation, expected in (
            ("missing", "authority_partial"),
            ("dirty", "authority_dirty_artifact"),
        ):
            pulled = self.pulled_copy()
            path = pulled / "final/manifest.json"
            if mutation == "missing":
                path.unlink()
            else:
                path.write_text("{}\n", encoding="utf-8")
            with self.subTest(mutation=mutation):
                payload = json.loads(self.audit(pulled).stdout)
                self.assertEqual(payload["authority"]["classification"], expected)

    def test_workspace_validation_timeout_is_classified_separately(self) -> None:
        self.create_package()
        pulled = self.pulled_copy()
        slow = self.root / "slow-validator.py"
        slow.write_text(
            "import time\ntime.sleep(5)\n",
            encoding="utf-8",
        )
        completed = self.run_authority(
            "audit",
            "--source",
            str(pulled),
            "--workspace-helper",
            str(slow),
            "--timeout-seconds",
            "0.2",
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["authority"]["classification"], "authority_timeout")

    def test_staging_rejects_oversized_file_before_destination_growth(self) -> None:
        authority = load_authority_module()
        source = self.root / "oversized-source"
        target = self.root / "oversized-target"
        source.mkdir()
        (source / "large.bin").write_bytes(b"12345")

        with self.assertRaises(authority.AuthorityError) as raised:
            authority.stage_tree_bounded(
                source,
                target,
                deadline=float("inf"),
                max_files=1,
                max_bytes=4,
            )

        self.assertEqual(raised.exception.classification, "authority_stage_bounds")
        self.assertFalse((target / "large.bin").exists())

    def test_staging_classifies_source_open_race_as_partial(self) -> None:
        authority = load_authority_module()
        source = self.root / "vanishing-source"
        target = self.root / "vanishing-target"
        source.mkdir()
        vanishing = source / "vanishing.bin"
        vanishing.write_bytes(b"bytes")
        original_open = Path.open

        def open_with_race(path: Path, *args, **kwargs):
            if path == vanishing and args and args[0] == "rb":
                vanishing.unlink()
                raise FileNotFoundError(vanishing)
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", open_with_race):
            with self.assertRaises(authority.AuthorityError) as raised:
                authority.stage_tree_bounded(
                    source,
                    target,
                    deadline=float("inf"),
                    max_files=1,
                    max_bytes=5,
                )

        self.assertEqual(raised.exception.classification, "authority_partial")
        self.assertFalse((target / "vanishing.bin").exists())

    def test_staging_enforces_growth_bound_before_each_chunk_write(self) -> None:
        authority = load_authority_module()
        source = self.root / "growing-source"
        target = self.root / "growing-target"
        source.mkdir()
        growing = source / "growing.bin"
        growing.write_bytes(b"12345678")
        original_lstat = Path.lstat

        def stale_lstat(path: Path, *args, **kwargs):
            result = original_lstat(path, *args, **kwargs)
            if path == growing:
                values = list(result)
                values[6] = 4
                return os.stat_result(values)
            return result

        with mock.patch.object(Path, "lstat", stale_lstat):
            with self.assertRaises(authority.AuthorityError) as raised:
                authority.stage_tree_bounded(
                    source,
                    target,
                    deadline=float("inf"),
                    max_files=1,
                    max_bytes=6,
                )

        self.assertEqual(raised.exception.classification, "authority_stage_bounds")
        self.assertLessEqual((target / "growing.bin").stat().st_size, 6)

    def test_audit_materializes_transferred_lfs_content_without_remote_store(self) -> None:
        lfs = subprocess.run(
            ["git", "lfs", "version"],
            check=False,
            capture_output=True,
            text=True,
        )
        if lfs.returncode != 0:
            self.skipTest("git-lfs is unavailable")
        self.run_git("lfs", "install", "--local")
        (self.workspace / ".gitattributes").write_text(
            "*.bin filter=lfs diff=lfs merge=lfs -text\n",
            encoding="utf-8",
        )
        artifact = self.workspace / "final/artifact.bin"
        artifact.write_bytes(b"actual-lfs-artifact-bytes")
        self.run_git("add", ".gitattributes", "final/artifact.bin")
        self.run_git("commit", "--quiet", "-m", "workspace: publish LFS artifact")
        self.create_package()
        pulled = self.pulled_copy()

        audited = self.audit(pulled)

        self.assertEqual(audited.returncode, 0, audited.stderr)
        self.assertEqual(json.loads(audited.stdout)["authority"]["mode"], "materialized")


if __name__ == "__main__":
    unittest.main()
