from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.pilot.trusted_tools import (
    MANIFEST_RELATIVE,
    SCHEMA,
    TrustedToolsError,
    manifest_bytes,
    validate_trusted_tools,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS = (
    "skills/cad/scripts/canonical-build",
    "skills/cad/scripts/packages/cadgen/src/cadgen",
    "skills/mesh-compare/scripts/packages/meshscope/src/meshscope",
    "skills/mesh-compare/scripts/packages/meshshot/src/meshshot",
)


class TrustedToolsTests(unittest.TestCase):
    def test_repository_manifest_matches_fixed_inventory(self) -> None:
        validate_trusted_tools(REPO_ROOT)
        value = json.loads((REPO_ROOT / MANIFEST_RELATIVE).read_text())
        self.assertEqual(SCHEMA, value["schema"])
        paths = {item["path"] for item in value["files"]}
        self.assertIn("canonical-build/__main__.py", paths)
        self.assertIn("packages/cadgen/src/cadgen/__init__.py", paths)
        self.assertIn("packages/meshscope/src/meshscope/__init__.py", paths)
        self.assertIn("packages/meshshot/src/meshshot/__init__.py", paths)

    def test_changed_or_extra_file_invalidates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            for relative in SOURCE_DIRS:
                directory = root / relative
                directory.mkdir(parents=True)
                (directory / "runtime.py").write_text("value = 1\n")
            manifest = root / MANIFEST_RELATIVE
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes(manifest_bytes(root))
            validate_trusted_tools(root)
            (root / SOURCE_DIRS[0] / "runtime.py").write_text("value = 2\n")
            with self.assertRaises(TrustedToolsError):
                validate_trusted_tools(root)
            manifest.write_bytes(manifest_bytes(root))
            (root / SOURCE_DIRS[0] / "extra.py").write_text("extra = True\n")
            with self.assertRaises(TrustedToolsError):
                validate_trusted_tools(root)

    def test_platform_build_outputs_do_not_change_fixed_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as text:
            root = Path(text)
            for relative in SOURCE_DIRS:
                directory = root / relative
                directory.mkdir(parents=True)
                (directory / "runtime.py").write_text("value = 1\n")
            before = manifest_bytes(root)
            meshscope = root / SOURCE_DIRS[2]
            (meshscope / "_native.cpp").write_text("generated source\n")
            (meshscope / "_native.cpython-312-darwin.so").write_bytes(b"native")
            (meshscope / ".DS_Store").write_bytes(b"finder")
            self.assertEqual(before, manifest_bytes(root))


if __name__ == "__main__":
    unittest.main()
