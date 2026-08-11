from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from scripts.pilot import deployment_authority


class DeploymentAuthorityTests(unittest.TestCase):
    def test_complete_receipt_materializes_and_reverifies_actual_retained_files(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
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


if __name__ == "__main__":
    unittest.main()
