"""Real-container semantic test for the sole residual browser path.

Set ``TTC_BROWSER_RUNTIME_TEST_IMAGE`` to an exact ``sha256:<64>`` image ID.
The ordinary unit suite skips this test because it starts Docker and Chromium.
"""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PIL import Image

from tests.python.support.paths import add_repo_path

add_repo_path("packages/browser_runtime/src")
add_repo_path("packages/meshshot/src")

from browser_runtime import BrowserRuntimeJob  # noqa: E402
from meshshot import MeshGeometry, render_residual_preview  # noqa: E402
from meshshot import runtime_client  # noqa: E402


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _triangle(offset: float) -> MeshGeometry:
    return MeshGeometry(
        vertices=[
            [-0.55 + offset, -0.45, 0.0],
            [0.55 + offset, -0.45, 0.0],
            [0.0 + offset, 0.55, 0.0],
        ],
        faces=[[0, 1, 2]],
    )


class BrowserRuntimeResidualIntegrationTests(unittest.TestCase):
    def test_real_runtime_is_semantic_and_repeatable(self) -> None:
        image_id = os.environ.get("TTC_BROWSER_RUNTIME_TEST_IMAGE", "")
        if _IMAGE_ID.fullmatch(image_id) is None:
            self.skipTest("exact Browser Runtime image ID was not supplied")

        with TemporaryDirectory() as temp:
            job = BrowserRuntimeJob.create(Path(temp), image_ref=image_id)
            try:
                job.start()
                job.preflight()
                capability_path = job.capability_dir / "runtime.json"
                with mock.patch.object(
                    runtime_client, "RUNTIME_CAPABILITY_PATH", capability_path
                ):
                    first = render_residual_preview(
                        _triangle(0.0), _triangle(0.18)
                    )
                    second = render_residual_preview(
                        _triangle(0.0), _triangle(0.18)
                    )

                self.assertEqual(first.png_bytes, second.png_bytes)
                self.assertEqual(first.views, second.views)
                self.assertEqual(len(first.views), 8)
                with Image.open(BytesIO(first.png_bytes)) as image:
                    self.assertEqual(image.size, (504, 1008))
                    pixels = list(image.convert("RGB").getdata())
                self.assertTrue(any(r > 100 and g < 80 for r, g, _ in pixels))
                self.assertTrue(any(g > 100 and r < 80 for r, g, _ in pixels))
                self.assertTrue(any(r > 100 and g > 100 for r, g, _ in pixels))
            finally:
                job.stop()


if __name__ == "__main__":
    unittest.main()
