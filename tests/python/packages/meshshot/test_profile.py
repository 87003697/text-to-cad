"""Public profile contract for the shared residual renderer."""

from __future__ import annotations

import unittest

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")

from meshshot import load_profile  # noqa: E402


class ResidualProfileTests(unittest.TestCase):
    def test_cadena_profile_freezes_views_dimensions_and_renderer_identity(self) -> None:
        loaded = load_profile()
        profile = loaded.profile

        self.assertEqual("cadena_residual_eight_view/1", profile["name"])
        self.assertEqual(
            ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            [view["name"] for view in profile["views"]],
        )
        self.assertEqual(
            [False, True, False, True, False, True, False, False],
            [view["horizontal_flip"] for view in profile["views"]],
        )
        self.assertEqual([252, 252], profile["variants"]["step"]["tile_pixels"])
        self.assertEqual([504, 1008], profile["variants"]["step"]["image_pixels"])
        self.assertEqual(2, profile["variants"]["step"]["render_scale"])
        self.assertEqual([504, 504], profile["variants"]["final"]["tile_pixels"])
        self.assertEqual([1008, 2016], profile["variants"]["final"]["image_pixels"])
        self.assertEqual(1, profile["variants"]["final"]["render_scale"])
        self.assertEqual("trellis2_canonical/1", profile["canonical_frame"]["coordinate_contract"])
        self.assertEqual([0, 255, 0], profile["colors"]["reference"])
        self.assertEqual([255, 0, 0], profile["colors"]["candidate"])
        self.assertEqual([255, 255, 0], profile["colors"]["overlap"])
        self.assertEqual([0, 0, 0], profile["colors"]["background"])
        self.assertEqual(
            {"canonical_margin": 0.08, "policy": "fixed_reference_frame/1"},
            profile["padding"],
        )
        self.assertEqual("meshshot-three-webgl", profile["renderer"]["name"])
        self.assertEqual(1, profile["renderer"]["version"])
        self.assertIn("vertical_fov_degrees", profile["camera"]["perspective"])
        self.assertIn("directional_intensity", profile["lighting"])
        self.assertEqual(
            "browser_canvas_bilinear/1",
            profile["downsampling"]["method"],
        )
        self.assertRegex(loaded.sha256, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
