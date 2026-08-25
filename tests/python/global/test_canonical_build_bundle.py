"""Tests for the trusted canonical-build tool bundle materializer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from scripts.pilot.canonical_build_bundle import (
    BUILDER_TOOL_ENTRYPOINT,
    CanonicalBuildBundleError,
    materialize_canonical_build_bundle,
    validate_canonical_build_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class MaterializeCanonicalBuildBundleTests(unittest.TestCase):
    def test_materialize_produces_immutable_content_addressed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            lease = materialize_canonical_build_bundle(REPO_ROOT, cache)
            self.assertTrue(lease.bundle.is_dir())
            # Fixed entrypoint layout expected by the __main__ bootstrap.
            entrypoint = lease.bundle / BUILDER_TOOL_ENTRYPOINT / "__main__.py"
            self.assertTrue(entrypoint.is_file())
            self.assertFalse(entrypoint.is_symlink())
            # Vendored cadgen runtime is materialized under the packages
            # subtree the entrypoint bootstrap prepends to sys.path.
            self.assertTrue(
                (
                    lease.bundle
                    / "packages/cadgen/src/cadgen/canonical_build.py"
                ).is_file()
            )
            # The published bundle is read-only.
            self.assertFalse(lease.bundle.stat().st_mode & 0o222)
            self.assertFalse(entrypoint.stat().st_mode & 0o222)
            # Marker + manifest expose the identity as sha256.
            self.assertEqual(64, len(lease.identity))
            marker = json.loads(
                (lease.bundle / ".bundle-complete").read_text(encoding="ascii")
            )
            manifest = json.loads(
                (lease.bundle / ".bundle-manifest.json").read_text(encoding="ascii")
            )
            self.assertEqual(marker["identity"], lease.identity)
            self.assertEqual(manifest["identity"], lease.identity)
            self.assertEqual(
                "mesh-to-cad.canonical-build-bundle/1",
                marker["schema"],
            )
            self.assertEqual(
                "mesh-to-cad.canonical-build-bundle/1",
                manifest["schema"],
            )
            # No symlink anywhere in the bundle tree.
            for path in lease.bundle.rglob("*"):
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertEqual(1, path.stat().st_nlink)
            # Second materialization reuses the same identity/final tree.
            lease_two = materialize_canonical_build_bundle(REPO_ROOT, cache)
            self.assertEqual(lease.identity, lease_two.identity)
            self.assertEqual(lease.bundle, lease_two.bundle)
            # Post-materialization validation matches the returned identity.
            observed = validate_canonical_build_bundle(lease.bundle)
            self.assertEqual(lease.identity, observed)

    def test_materialize_rejects_symlinked_cache_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual"
            actual.mkdir()
            link = root / "cache-link"
            link.symlink_to(actual)
            with self.assertRaises(CanonicalBuildBundleError):
                materialize_canonical_build_bundle(REPO_ROOT, link)

    def test_validate_detects_bundle_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            lease = materialize_canonical_build_bundle(REPO_ROOT, cache)
            # Loosen write permissions to allow tampering.
            for path in lease.bundle.rglob("*"):
                if path.is_dir():
                    os.chmod(path, 0o755)
                else:
                    os.chmod(path, 0o644)
            os.chmod(lease.bundle, 0o755)
            main = lease.bundle / BUILDER_TOOL_ENTRYPOINT / "__main__.py"
            main.write_bytes(main.read_bytes() + b"\n# tampered\n")
            with self.assertRaises(CanonicalBuildBundleError):
                validate_canonical_build_bundle(lease.bundle)

    def test_validate_rejects_extra_file_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            lease = materialize_canonical_build_bundle(REPO_ROOT, cache)
            os.chmod(lease.bundle, 0o755)
            (lease.bundle / "extra.py").write_bytes(b"unauthorized\n")
            with self.assertRaises(CanonicalBuildBundleError):
                validate_canonical_build_bundle(lease.bundle)
            (lease.bundle / "extra.py").unlink()

            # Symlink components inside the bundle are also rejected.
            os.chmod(lease.bundle / BUILDER_TOOL_ENTRYPOINT, 0o755)
            (lease.bundle / BUILDER_TOOL_ENTRYPOINT / "shortcut.py").symlink_to(
                lease.bundle / BUILDER_TOOL_ENTRYPOINT / "__main__.py"
            )
            with self.assertRaises(CanonicalBuildBundleError):
                validate_canonical_build_bundle(lease.bundle)


if __name__ == "__main__":
    unittest.main()
