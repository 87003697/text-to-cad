"""Semantic image tests through the public meshshot render API."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import statistics
import tempfile
from types import SimpleNamespace
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
    def test_development_runtime_capability_uses_sidecar_without_local_launch(self) -> None:
        from meshshot import renderer

        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        image = Image.new("RGB", (504, 1008), (17, 23, 31))
        encoded = BytesIO()
        image.save(encoded, format="PNG")
        result = {
            "ok": True,
            "pngDataUrl": "data:image/png;base64,"
            + base64.b64encode(encoded.getvalue()).decode("ascii"),
            "views": [
                {"name": name}
                for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capability_path = root / "runtime.json"
            capability_path.write_text(
                json.dumps(
                    {
                        "schema": "text-to-cad.browser-runtime-capability/1",
                        "jobId": "a" * 32,
                        "imageRef": "sha256:development-image",
                        "mcpUrl": "http://127.0.0.1:32001/mcp",
                        "cadRenderUrl": "http://127.0.0.1:32002/cad/render/residual",
                        "cadRenderToken": "b" * 64,
                        "programs": {"residual": "sha256:" + "c" * 64},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="ascii",
            )
            capability_path.chmod(0o444)
            with (
                mock.patch("meshshot.renderer._AUTHORITY_PATH", root / "absent-authority.json"),
                mock.patch("meshshot.renderer._RUNTIME_CAPABILITY_PATH", capability_path),
                mock.patch(
                    "meshshot.renderer._runtime_browser_render",
                    return_value=result,
                ) as runtime_render,
                mock.patch("meshshot.renderer._legacy_browser_render") as local_render,
            ):
                rendered = render_residual_preview(geometry, geometry)

        self.assertEqual(rendered.variant, "step")
        runtime_render.assert_called_once()
        local_render.assert_not_called()

    def test_invalid_development_runtime_capability_fails_without_local_fallback(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        with tempfile.TemporaryDirectory() as temp:
            capability_path = Path(temp) / "runtime.json"
            capability_path.write_text("{}", encoding="ascii")
            capability_path.chmod(0o444)
            with (
                mock.patch("meshshot.renderer._AUTHORITY_PATH", Path(temp) / "absent.json"),
                mock.patch("meshshot.renderer._RUNTIME_CAPABILITY_PATH", capability_path),
                mock.patch("meshshot.renderer._legacy_browser_render") as local_render,
            ):
                with self.assertRaisesRegex(MeshshotError, "runtime capability"):
                    render_residual_preview(geometry, geometry)
        local_render.assert_not_called()

    def test_development_runtime_request_is_closed_and_identity_bound(self) -> None:
        from meshshot import renderer

        capability = {
            "schema": "text-to-cad.browser-runtime-capability/1",
            "jobId": "a" * 32,
            "imageRef": "sha256:development-image",
            "mcpUrl": "http://127.0.0.1:32001/mcp",
            "cadRenderUrl": "http://127.0.0.1:32002/cad/render/residual",
            "cadRenderToken": "b" * 64,
            "programs": {"residual": "sha256:" + "c" * 64},
        }
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        loaded, payload, _ = renderer.broker_client.prepare_payload(
            _geometry(triangle), _geometry(triangle), "step", ()
        )
        result = {"ok": True, "pngDataUrl": "data:image/png;base64,AA==", "views": []}
        response_value = {
            "schema": "text-to-cad.cad-render-response/1",
            "jobId": capability["jobId"],
            "program": "residual",
            "programDigest": capability["programs"]["residual"],
            "result": result,
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return json.dumps(response_value).encode("ascii")

        with mock.patch.object(
            renderer._LOOPBACK_OPENER,
            "open",
            return_value=Response(),
        ) as open_request:
            observed = renderer._runtime_browser_render(capability, payload)

        self.assertEqual(observed, result)
        request = open_request.call_args.args[0]
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer " + capability["cadRenderToken"],
        )
        request_value = json.loads(request.data)
        self.assertEqual(
            set(request_value),
            {"schema", "jobId", "program", "programDigest", "payload"},
        )
        self.assertNotIn("profile", request_value["payload"])
        self.assertEqual(loaded.profile["variants"]["step"]["image_pixels"], [504, 1008])

    def test_development_runtime_capability_rejects_malformed_port(self) -> None:
        from meshshot import renderer

        with tempfile.TemporaryDirectory() as temp:
            capability_path = Path(temp) / "runtime.json"
            capability_path.write_text(
                json.dumps(
                    {
                        "schema": "text-to-cad.browser-runtime-capability/1",
                        "jobId": "a" * 32,
                        "imageRef": "sha256:development-image",
                        "mcpUrl": "http://127.0.0.1:not-a-port/mcp",
                        "cadRenderUrl": "http://127.0.0.1:32002/cad/render/residual",
                        "cadRenderToken": "b" * 64,
                        "programs": {"residual": "sha256:" + "c" * 64},
                    }
                ),
                encoding="ascii",
            )
            capability_path.chmod(0o444)
            with mock.patch("meshshot.renderer._RUNTIME_CAPABILITY_PATH", capability_path):
                with self.assertRaisesRegex(MeshshotError, "identity is invalid"):
                    renderer._load_runtime_capability()

    def test_development_runtime_uses_proxy_free_loopback_client(self) -> None:
        from meshshot import renderer

        proxy_handlers = [
            handler for handler in renderer._LOOPBACK_OPENER.handlers
            if isinstance(handler, renderer.urllib_request.ProxyHandler)
        ]
        # Supplying ProxyHandler({}) suppresses urllib's default environment-
        # aware ProxyHandler; build_opener intentionally omits the empty one.
        self.assertEqual(proxy_handlers, [])

    def test_legacy_render_delegates_deadline_to_caller(self) -> None:
        from meshshot import renderer

        page = mock.MagicMock()
        page.evaluate.return_value = {"ok": True}
        context = mock.MagicMock()
        context.new_page.return_value = page
        browser = mock.MagicMock()
        browser.new_context.return_value = context
        playwright = mock.MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright
        manager.__exit__.return_value = False

        with mock.patch(
            "playwright.sync_api.sync_playwright",
            return_value=manager,
        ):
            result = renderer._legacy_browser_render({"candidate": "payload"})

        self.assertEqual(result, {"ok": True})
        page.goto.assert_called_once_with(
            renderer._RENDER_URL,
            wait_until="load",
            timeout=0,
        )
        page.wait_for_function.assert_called_once_with(
            "typeof window.__meshshotRender === 'function'",
            timeout=0,
        )

    def test_formal_authority_rejects_symlink_without_local_fallback(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text(
                json.dumps(
                    {
                        "schema": "meshshot.browser-authority/1",
                        "jobId": "formal-job-1",
                        "gateNonce": "a" * 32,
                        "imageId": "sha256:"
                        + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
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
                    {"MESHSHOT_BROWSER_AUTHORITY_FILE": str(target)},
                    clear=False,
                ),
                mock.patch("meshshot.renderer._AUTHORITY_PATH", authority),
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
                        "gateNonce": "a" * 32,
                        "imageId": "sha256:"
                        + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
                        "programs": {
                            "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
                            "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
                        },
                    }
                ),
                encoding="utf-8",
            )
            authority_path.chmod(0o444)
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
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("meshshot.renderer._AUTHORITY_PATH", authority_path),
                mock.patch(
                    "meshshot.renderer.os.open",
                    wraps=os.open,
                ) as authority_open,
                mock.patch("playwright.sync_api.sync_playwright") as local_browser,
                mock.patch("meshshot.renderer.socket.socket", return_value=connection),
            ):
                rendered = render_residual_preview(geometry, geometry, variant="step")

        local_browser.assert_not_called()
        opened_path, opened_flags = authority_open.call_args.args
        self.assertEqual(opened_path, authority_path)
        self.assertTrue(opened_flags & getattr(os, "O_NOFOLLOW", 0))
        self.assertEqual(rendered.variant, "step")
        self.assertEqual(view_names, [view["name"] for view in rendered.views])
        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)
        observed = json.loads(connection.sent)
        self.assertEqual(
            set(observed),
            {"schema", "jobId", "imageId", "program", "payload"},
        )
        self.assertEqual(observed["program"], "residual")
        self.assertEqual(connection.path, "/run/meshshot-browser/browser.sock")
        self.assertEqual(
            observed["payload"]["options"],
            {"cameraPolicy": "profile-fixed", "canonicalPostprocess": True},
        )

    def test_attacker_env_authority_cannot_redirect_or_select_formal_mode(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        legacy = {
            "ok": True,
            "pngDataUrl": "data:image/png;base64,"
            + base64.b64encode(Image.new("RGB", (504, 1008)).tobytes()).decode("ascii"),
            "views": [
                {"name": name}
                for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }
        image = Image.new("RGB", (504, 1008), (1, 2, 3))
        encoded = BytesIO()
        image.save(encoded, format="PNG")
        legacy["pngDataUrl"] = "data:image/png;base64," + base64.b64encode(
            encoded.getvalue()
        ).decode("ascii")
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            attacker = Path(temp) / "authority.json"
            attacker.write_text(
                json.dumps(
                    {
                        "schema": "meshshot.browser-authority/1",
                        "jobId": "formal-job-1",
                        "gateNonce": "a" * 32,
                        "imageId": "sha256:"
                        + "22ff2413ffd9dcdb5f62e5dbb2c6e46d6b4e98f0e45dc4698f80eb8f06b146f1",
                        "programs": {
                            "residual": "d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
                            "viewer": "e2e1bfd1a28c4ef7ce312f477a301f8ef5386ecbcb64eb5d586b29bcdbb4728b",
                        },
                    }
                ),
                encoding="utf-8",
            )
            attacker.chmod(0o444)
            attacker_socket_path = Path(temp) / "browser.sock"
            attacker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            attacker_socket.bind(os.fspath(attacker_socket_path))
            fixed_absent = Path(temp) / "fixed-mount-absent.json"
            try:
                with (
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_BROWSER_AUTHORITY_FILE": str(attacker)},
                        clear=False,
                    ),
                    mock.patch("meshshot.renderer._AUTHORITY_PATH", fixed_absent),
                    mock.patch(
                        "meshshot.renderer._legacy_browser_render",
                        return_value=legacy,
                    ) as legacy_render,
                    mock.patch("meshshot.renderer.socket.socket") as formal_socket,
                ):
                    rendered = render_residual_preview(geometry, geometry)
            finally:
                attacker_socket.close()
        self.assertEqual(rendered.variant, "step")
        legacy_render.assert_called_once()
        formal_socket.assert_not_called()

    def test_formal_authority_open_is_nofollow_and_fixed_contract_is_package_owned(self) -> None:
        from meshshot import renderer

        self.assertEqual(
            renderer._AUTHORITY_PATH,
            Path("/run/meshshot-browser/authority.json"),
        )
        self.assertEqual(
            renderer._SOCKET_PATH,
            Path("/run/meshshot-browser/browser.sock"),
        )
        self.assertTrue(
            (Path(renderer.__file__).resolve().parent / "browser_contract.json").is_file()
        )

    def test_formal_authority_rejects_replaceable_inode_metadata(self) -> None:
        """The public seam rejects wrong owner, mode, or link count without fallback."""

        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)
        with tempfile.TemporaryDirectory() as temp:
            authority = Path(temp) / "authority.json"
            authority.write_text("{}", encoding="utf-8")
            authority.chmod(0o444)
            actual = authority.stat()
            variants = {
                "owner": {"st_uid": actual.st_uid + 1},
                "mode": {"st_mode": actual.st_mode | 0o200},
                "links": {"st_nlink": 2},
            }
            for label, change in variants.items():
                values = {
                    "st_mode": actual.st_mode,
                    "st_nlink": actual.st_nlink,
                    "st_uid": actual.st_uid,
                }
                values.update(change)
                metadata = SimpleNamespace(**values)
                with self.subTest(label=label):
                    with (
                        mock.patch("meshshot.renderer._AUTHORITY_PATH", authority),
                        mock.patch("meshshot.renderer.os.fstat", return_value=metadata),
                        mock.patch(
                            "playwright.sync_api.sync_playwright"
                        ) as local_browser,
                    ):
                        with self.assertRaisesRegex(MeshshotError, "replaceable"):
                            render_residual_preview(geometry, geometry)
                    local_browser.assert_not_called()

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
