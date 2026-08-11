from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.python.support.paths import REPO_ROOT


MODULE_PATH = REPO_ROOT / "scripts/pilot/snapshot_receipt.py"
SPEC = importlib.util.spec_from_file_location("snapshot_receipt", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
snapshot_receipt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = snapshot_receipt
SPEC.loader.exec_module(snapshot_receipt)


class SnapshotReceiptTests(unittest.TestCase):
    def test_snapshot_batch_writes_checks_and_verifies_uploaded_receipt(self) -> None:
        script = (REPO_ROOT / "scripts/pilot/snapshot-batch.sh").read_text(
            encoding="utf-8"
        )
        write = 'snapshot_receipt.py" write "$STAGING"'
        check = 'snapshot_receipt.py" check "$STAGING"'
        upload = 'aws s3 cp --recursive --only-show-errors "$STAGING/"'
        self.assertIn(write, script)
        self.assertIn(check, script)
        self.assertIn("snapshot-receipt.json", script)
        self.assertIn("REMOTE_RECEIPT_SHA", script)
        self.assertIn("REMOTE_OBJECT_COUNT", script)
        self.assertLess(script.index(write), script.index(upload))
        self.assertLess(script.index(check), script.index(upload))

    def test_receipt_digests_every_shipped_file_and_verifies_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            (root / "HEAD.sha").write_text("a" * 40 + "\n", encoding="utf-8")
            (root / "dirty.diff").write_text("diff\n", encoding="utf-8")
            nested = root / "skills/example/SKILL.md"
            nested.parent.mkdir(parents=True)
            nested.write_text("# Example\n", encoding="utf-8")

            receipt = snapshot_receipt.write_receipt(root)

            self.assertEqual("pilot.shipped-tree-receipt/1", receipt["schema"])
            self.assertEqual("a" * 40, receipt["source_head"])
            self.assertEqual(3, receipt["file_count"])
            self.assertEqual(
                ["HEAD.sha", "dirty.diff", "skills/example/SKILL.md"],
                [item["path"] for item in receipt["files"]],
            )
            for item in receipt["files"]:
                self.assertEqual(
                    hashlib.sha256((root / item["path"]).read_bytes()).hexdigest(),
                    item["sha256"],
                )
            self.assertEqual(receipt, snapshot_receipt.verify_receipt(root))
            self.assertEqual(
                receipt,
                json.loads((root / "snapshot-receipt.json").read_text(encoding="utf-8")),
            )

    def test_verification_rejects_changed_missing_or_unreceipted_files(self) -> None:
        for mutation in ("changed", "missing", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root_text:
                root = Path(root_text)
                (root / "HEAD.sha").write_text("b" * 40 + "\n", encoding="utf-8")
                payload = root / "payload.txt"
                payload.write_text("payload\n", encoding="utf-8")
                snapshot_receipt.write_receipt(root)
                if mutation == "changed":
                    payload.write_text("changed\n", encoding="utf-8")
                elif mutation == "missing":
                    payload.unlink()
                else:
                    (root / "extra.txt").write_text("extra\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    snapshot_receipt.SnapshotReceiptError,
                    "shipped tree does not match snapshot receipt",
                ):
                    snapshot_receipt.verify_receipt(root)


if __name__ == "__main__":
    unittest.main()
