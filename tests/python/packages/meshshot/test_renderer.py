"""Semantic image tests through the public meshshot render API."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import statistics
import subprocess
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
    def test_public_render_prelaunches_attested_browser_and_attaches_over_loopback_cdp(
        self,
    ) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright.chromium.connect_over_cdp.return_value = browser
        playwright.chromium.launch.side_effect = AssertionError(
            "Playwright must not own the production browser process"
        )

        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": f"data:image/png;base64,{encoded}",
            "views": [{} for _ in range(8)],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_bytes(b"attested-browser")
            executable.chmod(0o755)
            profile = root / "runtime-profile"

            def prelaunch(*_args: object, **_kwargs: object) -> object:
                profile.mkdir()
                (profile / "DevToolsActivePort").write_text(
                    "49152\n/devtools/browser/01234567-89ab-cdef-0123-456789abcdef\n",
                    encoding="utf-8",
                )
                return process

            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch(
                    "playwright.sync_api.sync_playwright", sync_playwright
                ),
                mock.patch("subprocess.Popen", side_effect=prelaunch),
                mock.patch("tempfile.mkdtemp", return_value=os.fspath(profile)),
                mock.patch("os.getpgid", return_value=43210),
                mock.patch("os.killpg") as killpg,
            ):
                rendered = render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

        playwright.chromium.launch.assert_not_called()
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:49152",
            timeout=mock.ANY,
            is_local=True,
        )
        self.assertEqual("meshshot.prelaunched-cdp-runtime/1", rendered.browser_runtime["schema"])
        self.assertEqual("passed", rendered.browser_runtime["result"])
        self.assertEqual(
            {"name", "sha256"},
            set(rendered.browser_runtime["adapter_profile"]),
        )
        self.assertEqual(
            {"playwright", "browser", "revision", "version", "sha256"},
            set(rendered.browser_runtime["browser_identity"]),
        )
        self.assertFalse(profile.exists())
        self.assertIn(mock.call(43210, __import__("signal").SIGTERM), killpg.mock_calls)

    def test_real_playwright_launches_explicit_attested_executable(self) -> None:
        """Exercise the production executable_path at the real launch seam."""

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path).resolve(
                strict=True
            )
        triangle = (
            (-0.2, -0.2, 0.0),
            (0.2, -0.2, 0.0),
            (0.0, 0.2, 0.0),
        )
        geometry = _geometry(triangle)

        with mock.patch.dict(
            os.environ,
            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
        ):
            rendered = render_residual_preview(
                geometry,
                geometry,
                variant="step",
            )

        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)

    def test_explicit_attested_browser_executable_bypasses_registry_selection(
        self,
    ) -> None:
        playwright = mock.MagicMock()
        playwright.chromium.launch.side_effect = RuntimeError("stop after launch")
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_bytes(b"browser")
            executable.chmod(0o755)
            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch(
                    "playwright.sync_api.sync_playwright", sync_playwright
                ),
                self.assertRaises(MeshshotError),
            ):
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

            playwright.chromium.launch.assert_called_once_with(
                headless=True,
                args=["--no-sandbox"],
                timeout=15_000,
                executable_path=os.fspath(executable),
            )

    def test_explicit_browser_executable_rejects_unsafe_file_types(self) -> None:
        playwright = mock.MagicMock()
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular"
            regular.write_bytes(b"browser")
            regular.chmod(0o644)
            symlink = root / "symlink"
            symlink.symlink_to(regular)
            cases = (
                "relative-browser",
                os.fspath(root / "missing"),
                os.fspath(root),
                os.fspath(regular),
                os.fspath(symlink),
            )

            for value in cases:
                with (
                    self.subTest(value=value),
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_BROWSER_EXECUTABLE": value},
                    ),
                    mock.patch(
                        "playwright.sync_api.sync_playwright", sync_playwright
                    ),
                    self.assertRaises(MeshshotError) as raised,
                ):
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )

                self.assertEqual(
                    "browser_launch_executable", raised.exception.phase
                )

        playwright.chromium.launch.assert_not_called()

    def test_browser_launch_failure_has_resource_specific_closed_phase(self) -> None:
        cases = {
            "pthread_create: Resource temporarily unavailable": (
                "browser_launch_process_limit"
            ),
            "Error: spawn /usr/bin/chromium EAGAIN": (
                "browser_launch_process_limit"
            ),
            "Too many open files": "browser_launch_file_limit",
            "Error: spawn /usr/bin/chromium ENFILE": "browser_launch_file_limit",
            "Cannot allocate memory": "browser_launch_address_space",
            "Error: spawn /usr/bin/chromium ENOMEM": (
                "browser_launch_address_space"
            ),
            "Creating shared memory in /dev/shm failed": (
                "browser_launch_shared_memory"
            ),
            "error while loading shared libraries": (
                "browser_launch_executable_dependency"
            ),
            "Error: spawn /missing/chromium ENOENT": (
                "browser_launch_executable_missing"
            ),
            "Error: spawn /denied/chromium EACCES": (
                "browser_launch_executable_spawn_permission"
            ),
            (
                "Error: spawn /denied/chromium --no-sandbox "
                "--user-data-dir=/tmp/pw EACCES"
            ): "browser_launch_executable_spawn_permission",
            "zygote sandbox initialization: Permission denied": (
                "browser_launch_sandbox_permission"
            ),
            "cannot create user data directory: Permission denied": (
                "browser_launch_filesystem_permission"
            ),
            "Failed to create user-data-dir: EROFS": (
                "browser_launch_filesystem_permission"
            ),
            "Profile directory is on a read-only file system": (
                "browser_launch_filesystem_permission"
            ),
            "browser startup: Permission denied": (
                "browser_launch_executable_permission"
            ),
            "posix_spawn: No such file or directory": (
                "browser_launch_executable_missing"
            ),
        }
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                playwright = mock.MagicMock()
                playwright.chromium.launch.side_effect = RuntimeError(detail)
                sync_playwright = mock.MagicMock()
                sync_playwright.return_value.__enter__.return_value = playwright

                with (
                    mock.patch(
                        "playwright.sync_api.sync_playwright", sync_playwright
                    ),
                    self.assertRaises(MeshshotError) as raised,
                ):
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )

                self.assertEqual(expected, raised.exception.phase)

    def test_browser_launch_failure_has_closed_phase(self) -> None:
        playwright = mock.MagicMock()
        playwright.chromium.launch.side_effect = RuntimeError("sensitive launch detail")
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with (
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("browser_launch", raised.exception.phase)

    def test_invalid_browser_image_size_has_browser_result_phase(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright.chromium.launch.return_value = browser

        png = BytesIO()
        Image.new("RGB", (1, 1), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": f"data:image/png;base64,{encoded}",
            "views": [{} for _ in range(8)],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with (
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("browser_result", raised.exception.phase)

    def test_geometry_payload_crosses_same_origin_route_not_evaluate_arguments(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright.chromium.launch.return_value = browser

        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        routed_payload: dict[str, object] = {}

        def evaluate_without_payload(script: str, *args: object) -> dict[str, object]:
            self.assertIn('fetch("/payload.json"', script)
            self.assertEqual((), args)
            route_handler = page.route.call_args.args[1]
            route = mock.MagicMock()
            route.request.method = "GET"
            route.request.url = "http://meshshot.local/payload.json"
            route_handler(route)
            fulfilled = route.fulfill.call_args.kwargs
            self.assertEqual("application/json", fulfilled["content_type"])
            routed_payload.update(json.loads(fulfilled["body"]))
            return {
                "ok": True,
                "pngDataUrl": f"data:image/png;base64,{encoded}",
                "views": [{} for _ in range(8)],
            }

        page.evaluate.side_effect = evaluate_without_payload
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with mock.patch("playwright.sync_api.sync_playwright", sync_playwright):
            rendered = render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)
        self.assertEqual(
            [[-0.2, -0.2, 0.0], [0.2, -0.2, 0.0], [0.0, 0.2, 0.0]],
            routed_payload["reference"]["vertices"],
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
