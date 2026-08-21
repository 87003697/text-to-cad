from pathlib import Path
import hashlib
import unittest

from browser_runtime.config import CAD_RENDER_PROGRAMS
from tests.python.support.paths import REPO_ROOT


class BrowserRuntimeImageEntrypointTests(unittest.TestCase):
    def test_uses_preinstalled_runtime_binaries_without_npx(self) -> None:
        entrypoint = (
            REPO_ROOT / "packages/browser_runtime/image/entrypoint.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("/usr/bin/playwright-mcp", entrypoint)
        self.assertIn("node /opt/text-to-cad/cad-render-service.cjs", entrypoint)
        self.assertNotIn("npx", entrypoint)
        self.assertIn("wait -n", entrypoint)

    def test_image_build_asserts_and_bakes_fixed_program(self) -> None:
        dockerfile = (
            REPO_ROOT / "packages/browser_runtime/image/Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn("test -x /usr/bin/playwright-mcp", dockerfile)
        self.assertIn("playwright-core@1.51.1", dockerfile)
        self.assertIn("cad-render-service.cjs", dockerfile)
        self.assertIn("residual-render.js", dockerfile)
        self.assertIn("cadena_residual_eight_view_v1.json", dockerfile)
        self.assertIn('LABEL org.opencontainers.image.revision="$SOURCE_REVISION"', dockerfile)
        self.assertIn('RUN test -n "$SOURCE_REVISION"', dockerfile)

    def test_fixed_program_digest_covers_service_and_baked_assets(self) -> None:
        paths = (
            REPO_ROOT / "packages/browser_runtime/image/cad-render-service.cjs",
            REPO_ROOT / "packages/meshshot/src/meshshot/runtime/render.html",
            REPO_ROOT / "packages/meshshot/src/meshshot/runtime/residual-render.js",
            REPO_ROOT
            / "packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json",
        )
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.read_bytes())
        self.assertEqual(
            CAD_RENDER_PROGRAMS["residual"],
            "sha256:" + digest.hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
