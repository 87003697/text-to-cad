from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class OfflineBundleContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (REPO_ROOT / relative).read_text()

    def test_installed_plugin_smoke_disables_dependency_installation(self) -> None:
        wrapper = self.read("scripts/release/smoke-installed-plugin.sh")
        self.assertIn("BUNDLE_INSTALL_DEPS=0", wrapper)

    def test_node_builder_fails_before_npm_install_when_offline(self) -> None:
        helper = self.read("scripts/bundle/lib/node_builders.sh")
        offline = helper.index('BUNDLE_INSTALL_DEPS:-1')
        install = helper.index("npm install --prefix")
        self.assertLess(offline, install)
        self.assertIn("Offline production bundling refuses to run npm install", helper)

    def test_mesh_compare_fails_before_npm_ci_when_offline(self) -> None:
        bundle = self.read("scripts/bundle/skills/bundle-mesh-compare.sh")
        offline = bundle.index('BUNDLE_INSTALL_DEPS:-1')
        install = bundle.index("npm ci --prefix")
        self.assertLess(offline, install)
        self.assertIn("Offline production bundling refuses to run npm ci", bundle)

    def test_every_snapshot_bundle_respects_offline_mode(self) -> None:
        expected = {
            "bundle-cad.sh": 'INSTALL_DEPS="${BUNDLE_INSTALL_DEPS:-1}"',
            "bundle-dxf.sh": '"${BUNDLE_INSTALL_DEPS:-1}"',
            "bundle-sdf.sh": '"${BUNDLE_INSTALL_DEPS:-1}"',
            "bundle-srdf.sh": '"${BUNDLE_INSTALL_DEPS:-1}"',
            "bundle-urdf.sh": '"${BUNDLE_INSTALL_DEPS:-1}"',
        }
        for name, marker in expected.items():
            with self.subTest(bundle=name):
                self.assertIn(marker, self.read(f"scripts/bundle/skills/{name}"))


if __name__ == "__main__":
    unittest.main()
