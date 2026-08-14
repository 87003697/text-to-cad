from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.pilot import deployment_authority


class DeploymentAuthorityTests(unittest.TestCase):
    def test_complete_receipt_materializes_and_reverifies_actual_retained_files(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text).resolve()
            (root / "scripts").mkdir()
            (root / "scripts/runner.py").write_text("runner\n", encoding="utf-8")
            (root / "native").mkdir()
            (root / "native/backend.so").write_bytes(b"native")
            (root / "scripts/__pycache__").mkdir()
            (root / "scripts/__pycache__/runner.pyc").write_bytes(b"cache")
            receipt = deployment_authority.build_receipt(
                root,
                source_head="a" * 40,
                contract_paths=("scripts", "native/backend.so"),
            )
            retained = root / "retained"

            deployment_authority.materialize_receipt(root, receipt, retained)

            self.assertIn("native/backend.so", [item["path"] for item in receipt["files"]])
            self.assertNotIn(
                "scripts/__pycache__/runner.pyc",
                [item["path"] for item in receipt["files"]],
            )
            self.assertTrue(receipt["exclusions"]["native_shared_objects_included"])
            self.assertEqual(
                receipt,
                deployment_authority.verify_materialized(retained, receipt),
            )
            (retained / "scripts/runner.py").write_text("forged\n", encoding="utf-8")
            with self.assertRaisesRegex(
                deployment_authority.DeploymentAuthorityError, "does not match"
            ):
                deployment_authority.verify_materialized(retained, receipt)

    def test_receipt_rejects_traversal_symlinks_and_special_files(self) -> None:
        for mutation in ("traversal", "symlink", "fifo"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root_text:
                root = Path(root_text)
                (root / "safe").mkdir()
                (root / "safe/file.txt").write_text("safe\n", encoding="utf-8")
                if mutation == "traversal":
                    paths = ("../outside",)
                elif mutation == "symlink":
                    (root / "safe/link").symlink_to(root / "safe/file.txt")
                    paths = ("safe",)
                else:
                    os.mkfifo(root / "safe/fifo")
                    paths = ("safe",)
                with self.assertRaises(deployment_authority.DeploymentAuthorityError):
                    deployment_authority.build_receipt(
                        root,
                        source_head="b" * 40,
                        contract_paths=paths,
                    )

    def test_receipt_rejects_a_symlink_ancestor_even_when_leaf_is_regular(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            real = root / "real/pilot"
            real.mkdir(parents=True)
            (real / "runner.py").write_text("runner\n", encoding="utf-8")
            (root / "scripts").symlink_to(root / "real", target_is_directory=True)

            with self.assertRaisesRegex(
                deployment_authority.DeploymentAuthorityError,
                "symlink|escape",
            ):
                deployment_authority.build_receipt(
                    root,
                    source_head="c" * 40,
                    contract_paths=("scripts/pilot",),
                )

    def test_runtime_identity_rejects_shadow_bwrap_and_wrong_chromium_cache(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            cadpy = root / deployment_authority.CADPY_RUNTIME_PATH
            cadpy.parent.mkdir(parents=True)
            cadpy.write_bytes(b"cadpy")
            identity = {
                "schema": "cvm.provider-free-runtime-identity/1",
                "bwrap": {
                    "path": "/usr/bin/bwrap",
                    "sha256": "a" * 64,
                    "version": "bubblewrap 1.2.3",
                },
                "chromium": {
                    "revision": "1234",
                    "host_cache_path": "/home/test/.cache/ms-playwright",
                    "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                    "executable_path": (
                        "/home/test/.cache/ms-playwright/"
                        "chromium_headless_shell-1234/"
                        "chrome-headless-shell-linux64/chrome-headless-shell"
                    ),
                    "sha256": "b" * 64,
                    "tree_manifest_sha256": "c" * 64,
                },
                "cadpy": {
                    "path": deployment_authority.CADPY_RUNTIME_PATH,
                    "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
                },
            }
            for mutation in (
                "bwrap",
                "revision",
                "browser-path",
                "tree-manifest",
            ):
                with self.subTest(mutation=mutation):
                    candidate = json.loads(json.dumps(identity))
                    if mutation == "bwrap":
                        candidate["bwrap"]["path"] = "/tmp/bwrap"
                    elif mutation == "revision":
                        candidate["chromium"]["revision"] = "9999"
                    elif mutation == "tree-manifest":
                        del candidate["chromium"]["tree_manifest_sha256"]
                    else:
                        candidate["chromium"]["executable_path"] = "/tmp/chromium"
                    with self.assertRaises(
                        deployment_authority.DeploymentAuthorityError
                    ):
                        deployment_authority.validate_runtime_identity(
                            root,
                            candidate,
                            verify_external=False,
                        )

    def test_runtime_identity_binds_every_browser_revision_resource(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            cadpy = root / deployment_authority.CADPY_RUNTIME_PATH
            cadpy.parent.mkdir(parents=True)
            cadpy.write_bytes(b"cadpy")
            home = root / "home"
            revision = home / (
                ".cache/ms-playwright/chromium_headless_shell-1234"
            )
            executable = revision / (
                "chrome-headless-shell-linux64/chrome-headless-shell"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"browser")
            executable.chmod(0o755)
            resource = revision / "resources.pak"
            resource.write_bytes(b"trusted resource")
            bwrap = (root / "bwrap").resolve()
            bwrap.write_text(
                "#!/bin/sh\nprintf 'bubblewrap 1.2.3\\n'\n",
                encoding="utf-8",
            )
            bwrap.chmod(0o755)

            with mock.patch.object(
                deployment_authority, "TRUSTED_BWRAP_PATH", bwrap
            ):
                identity = deployment_authority.probe_runtime_identity(
                    root,
                    chromium_revision="1234",
                    home=home,
                )
                self.assertRegex(
                    identity["chromium"]["tree_manifest_sha256"],
                    r"^[0-9a-f]{64}$",
                )
                resource.write_bytes(b"same-uid substituted resource")
                with self.assertRaisesRegex(
                    deployment_authority.DeploymentAuthorityError,
                    "browser tree|Chromium",
                ):
                    deployment_authority.validate_runtime_identity(
                        root,
                        identity,
                        verify_external=True,
                    )


if __name__ == "__main__":
    unittest.main()
