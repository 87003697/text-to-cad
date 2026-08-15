"""Semantic image tests through the public meshshot render API."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import statistics
import tempfile
import unittest
from unittest import mock

from PIL import Image

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")

from meshshot import MeshGeometry, MeshshotError, render_residual_preview  # noqa: E402


def _geometry(*triangles: tuple[tuple[float, float, float], ...]) -> MeshGeometry:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for triangle in triangles:
        start = len(vertices)
        vertices.extend([list(vertex) for vertex in triangle])
        faces.append([start, start + 1, start + 2])
    return MeshGeometry(vertices=vertices, faces=faces)


class ResidualRendererTests(unittest.TestCase):
    def test_formal_authority_rejects_symlink_without_local_fallback(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text(
                json.dumps(
                    {
                        "schema": "meshshot.browser-authority/1",
                        "jobId": "formal-job-1",
                        "imageId": "sha256:"
                        + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
                        "socketPath": "/run/meshshot-browser/browser-sidecar.sock",
                        "programs": {
                            "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
                            "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
                        },
                    }
                ),
                encoding="utf-8",
            )
            authority = root / "authority.json"
            authority.symlink_to(target)
            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_AUTHORITY_FILE": str(authority)},
                    clear=False,
                ),
                mock.patch("playwright.sync_api.sync_playwright") as local_browser,
            ):
                with self.assertRaisesRegex(MeshshotError, "authority file"):
                    render_residual_preview(geometry, geometry, variant="step")
        local_browser.assert_not_called()

    def test_formal_browser_authority_selects_registered_residual_program(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        view_names = ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            authority_path = root / "authority.json"
            authority_path.write_text(
                json.dumps(
                    {
                        "schema": "meshshot.browser-authority/1",
                        "jobId": "formal-job-1",
                        "imageId": "sha256:"
                        + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
                        "socketPath": "/workspace/repo/outputs/group/exp/run/browser.sock",
                        "programs": {
                            "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
                            "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
                        },
                    }
                ),
                encoding="utf-8",
            )
            image = Image.new("RGB", (504, 1008), (17, 23, 31))
            encoded = BytesIO()
            image.save(encoded, format="PNG")
            response = {
                "schema": "meshshot.browser-sidecar.render-response/1",
                "jobId": "formal-job-1",
                "imageId": "sha256:"
                + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
                "program": "residual",
                "result": {
                    "ok": True,
                    "pngDataUrl": "data:image/png;base64,"
                    + base64.b64encode(encoded.getvalue()).decode("ascii"),
                    "views": [{"name": name} for name in view_names],
                },
            }

            class FakeConnection:
                def __init__(self) -> None:
                    self.sent = b""
                    self.response = (
                        json.dumps(response, separators=(",", ":")).encode("ascii")
                        + b"\n"
                    )

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def settimeout(self, timeout):
                    self.timeout = timeout

                def connect(self, path):
                    self.path = path

                def sendall(self, payload):
                    self.sent += payload

                def shutdown(self, how):
                    self.shutdown_how = how

                def recv(self, size):
                    result, self.response = self.response[:size], self.response[size:]
                    return result

            connection = FakeConnection()
            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_AUTHORITY_FILE": str(authority_path)},
                    clear=False,
                ),
                mock.patch("playwright.sync_api.sync_playwright") as local_browser,
                mock.patch("meshshot.renderer.socket.socket", return_value=connection),
            ):
                rendered = render_residual_preview(geometry, geometry, variant="step")

        local_browser.assert_not_called()
        self.assertEqual(rendered.variant, "step")
        self.assertEqual(view_names, [view["name"] for view in rendered.views])
        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)
        observed = json.loads(connection.sent)
        self.assertEqual(
            set(observed),
            {"schema", "jobId", "imageId", "program", "payload"},
        )
        self.assertEqual(observed["program"], "residual")
        self.assertEqual(
            observed["payload"]["options"],
            {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True},
        )

    def test_step_render_exposes_eight_view_residual_channels_in_fixed_layout(self) -> None:
        shared = ((-0.12, -0.22, 0.0), (0.12, -0.22, 0.0), (0.0, 0.18, 0.0))
        reference_only = ((-0.46, -0.2, 0.0), (-0.2, -0.2, 0.0), (-0.33, 0.2, 0.0))
        candidate_only = ((0.2, -0.2, 0.0), (0.46, -0.2, 0.0), (0.33, 0.2, 0.0))

        rendered = render_residual_preview(
            _geometry(shared, reference_only),
            _geometry(shared, candidate_only),
            variant="step",
        )

        image = Image.open(BytesIO(rendered.png_bytes))
        self.assertEqual("RGB", image.mode)
        self.assertEqual((504, 1008), image.size)
        self.assertEqual(
            ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            [view["name"] for view in rendered.views],
        )
        first_tile = image.crop((0, 0, 252, 252))
        colors = first_tile.getcolors(maxcolors=252 * 252)
        self.assertIsNotNone(colors)
        present = {color for count, color in colors if count >= 8}
        self.assertTrue(any(red > 160 and green < 32 and blue < 32 for red, green, blue in present))
        self.assertTrue(any(green > 160 and red < 32 and blue < 32 for red, green, blue in present))
        self.assertTrue(any(red > 160 and green > 160 and blue < 32 for red, green, blue in present))

    def test_axial_depth_and_negative_flip_share_canonical_screen_framing(self) -> None:
        positive_z_left = (
            (-0.43, -0.18, 0.3),
            (-0.17, -0.18, 0.3),
            (-0.3, 0.18, 0.3),
        )
        negative_z_right = (
            (0.17, -0.18, -0.3),
            (0.43, -0.18, -0.3),
            (0.3, 0.18, -0.3),
        )
        geometry = _geometry(positive_z_left, negative_z_right)

        rendered = render_residual_preview(geometry, geometry, variant="step")
        image = Image.open(BytesIO(rendered.png_bytes))
        positive = image.crop((0, 0, 252, 252))
        negative = image.crop((252, 0, 504, 252))

        def brightness(tile: Image.Image, box: tuple[int, int, int, int]) -> float:
            cropped = tile.crop(box)
            values = [
                (red + green) / 2
                for y in range(cropped.height)
                for x in range(cropped.width)
                for red, green, blue in [cropped.getpixel((x, y))]
                if red > 32 and green > 32 and blue < 16
            ]
            self.assertGreater(len(values), 40)
            return statistics.mean(values)

        left = (36, 82, 102, 176)
        right = (150, 82, 216, 176)
        self.assertGreater(brightness(positive, left), brightness(positive, right) + 50)
        self.assertGreater(brightness(negative, right), brightness(negative, left) + 50)
        self.assertEqual(
            ["orthographic"] * 6 + ["perspective"] * 2,
            [view["framing"]["projection"] for view in rendered.views],
        )

    def test_same_environment_render_is_repeatable_and_final_size_is_frozen(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)

        first = render_residual_preview(geometry, geometry, variant="step")
        second = render_residual_preview(geometry, geometry, variant="step")
        final = render_residual_preview(geometry, geometry, variant="final")

        self.assertEqual(first.png_bytes, second.png_bytes)
        with Image.open(BytesIO(final.png_bytes)) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((1008, 2016), image.size)

    def test_all_negative_axial_flips_have_frozen_pixel_orientation(self) -> None:
        cases = (
            # view row, non-edge-on triangle, expected positive/negative side
            (
                0,
                ((0.22, -0.08, 0.0), (0.38, -0.08, 0.0), (0.30, 0.10, 0.0)),
                ("right", "right"),
            ),
            (
                1,
                ((0.22, 0.0, -0.08), (0.38, 0.0, -0.08), (0.30, 0.0, 0.10)),
                ("right", "left"),
            ),
            (
                2,
                ((0.0, -0.08, -0.38), (0.0, -0.08, -0.22), (0.0, 0.10, -0.30)),
                ("right", "right"),
            ),
        )

        for row, triangle, expected_sides in cases:
            with self.subTest(row=row):
                geometry = _geometry(triangle)
                rendered = render_residual_preview(geometry, geometry, variant="step")
                image = Image.open(BytesIO(rendered.png_bytes))
                for column, expected_side in enumerate(expected_sides):
                    tile = image.crop(
                        (column * 252, row * 252, (column + 1) * 252, (row + 1) * 252)
                    )
                    xs = [
                        pixel_x
                        for pixel_y in range(45, 207)
                        for pixel_x in range(30, 222)
                        for red, green, blue in [tile.getpixel((pixel_x, pixel_y))]
                        if red > 64 and green > 64 and blue < 16
                    ]
                    self.assertGreater(len(xs), 80)
                    mean_x = statistics.mean(xs)
                    if expected_side == "right":
                        self.assertGreater(mean_x, 145)
                    else:
                        self.assertLess(mean_x, 107)

    def test_candidate_never_autofits_or_changes_reference_owned_framing(self) -> None:
        reference = _geometry(
            ((-0.40, -0.18, 0.0), (-0.16, -0.18, 0.0), (-0.28, 0.20, 0.0))
        )
        candidate_in_frame = _geometry(
            ((0.16, -0.18, 0.0), (0.40, -0.18, 0.0), (0.28, 0.20, 0.0))
        )
        candidate_outside = _geometry(
            ((3.0, -0.18, 0.0), (3.24, -0.18, 0.0), (3.12, 0.20, 0.0))
        )

        inside = Image.open(
            BytesIO(
                render_residual_preview(
                    reference, candidate_in_frame, variant="step"
                ).png_bytes
            )
        )
        outside = Image.open(
            BytesIO(
                render_residual_preview(reference, candidate_outside, variant="step").png_bytes
            )
        )

        self.assertEqual(inside.getchannel("G").tobytes(), outside.getchannel("G").tobytes())
        inside_tile = inside.crop((25, 40, 227, 220))
        outside_tile = outside.crop((25, 40, 227, 220))
        inside_red = sum(
            1
            for pixel_y in range(inside_tile.height)
            for pixel_x in range(inside_tile.width)
            for red, green, blue in [inside_tile.getpixel((pixel_x, pixel_y))]
            if red > 64 and green < 32 and blue < 16
        )
        outside_red = sum(
            1
            for pixel_y in range(outside_tile.height)
            for pixel_x in range(outside_tile.width)
            for red, green, blue in [outside_tile.getpixel((pixel_x, pixel_y))]
            if red > 64 and green < 32 and blue < 16
        )
        self.assertGreater(inside_red, outside_red + 200)


if __name__ == "__main__":
    unittest.main()
