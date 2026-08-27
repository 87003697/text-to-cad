"""Contract tests for the one Browser Runtime meshshot client."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")
add_repo_path("packages/browser_runtime/src")

from browser_runtime.config import CAD_RENDER_PROGRAMS  # noqa: E402
from meshshot import MeshGeometry, MeshshotError, render_residual_preview  # noqa: E402
from meshshot import runtime_client  # noqa: E402


def _geometry() -> MeshGeometry:
    return MeshGeometry(
        vertices=[[-0.35, -0.3, 0.0], [0.35, -0.3, 0.0], [0.0, 0.35, 0.0]],
        faces=[[0, 1, 2]],
    )


def _capability() -> dict:
    return {
        "schema": "text-to-cad.browser-runtime-capability/1",
        "jobId": "a" * 32,
        "imageRef": "sha256:" + "d" * 64,
        "mcpUrl": "http://127.0.0.1:32001/mcp",
        "cadRenderUrl": "http://127.0.0.1:32002/cad/render/residual",
        "cadRenderToken": "b" * 64,
        "programs": {
            "residual": runtime_client.EXPECTED_RESIDUAL_PROGRAM,
            "snapshot": runtime_client.EXPECTED_SNAPSHOT_PROGRAM,
        },
    }


def _result() -> dict:
    image = Image.new("RGB", (504, 1008), (17, 23, 31))
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    return {
        "ok": True,
        "pngDataUrl": "data:image/png;base64,"
        + base64.b64encode(encoded.getvalue()).decode("ascii"),
        "views": [
            {"name": name}
            for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
        ],
    }


class BrowserRuntimeClientTests(unittest.TestCase):
    def test_registered_program_identity_matches_browser_runtime(self) -> None:
        self.assertEqual(
            runtime_client.EXPECTED_RESIDUAL_PROGRAM,
            CAD_RENDER_PROGRAMS["residual"],
        )

    def test_public_render_requires_runtime_capability(self) -> None:
        with mock.patch.object(
            runtime_client,
            "RUNTIME_CAPABILITY_PATH",
            Path("/definitely/absent/runtime.json"),
        ):
            with self.assertRaisesRegex(MeshshotError, "capability is required"):
                render_residual_preview(_geometry(), _geometry())
        with self.assertRaisesRegex(MeshshotError, "capability is required"):
            render_residual_preview(
                _geometry(),
                _geometry(),
                capability_path=Path("/definitely/absent/runtime.json"),
            )

    def test_packed_geometry_rejects_float32_overflow(self) -> None:
        invalid = MeshGeometry(
            vertices=[[1e39, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            faces=[[0, 1, 2]],
        )
        with self.assertRaisesRegex(MeshshotError, "finite three-dimensional"):
            invalid.to_packed_json()

    def test_public_render_has_one_runtime_path(self) -> None:
        with (
            mock.patch.object(
                runtime_client, "load_runtime_capability", return_value=_capability()
            ) as load_capability,
            mock.patch.object(
                runtime_client, "render_with_runtime", return_value=_result()
            ) as render_runtime,
        ):
            rendered = render_residual_preview(_geometry(), _geometry())
        load_capability.assert_called_once_with()
        render_runtime.assert_called_once()
        self.assertEqual(rendered.variant, "step")
        self.assertEqual(len(rendered.views), 8)

    def test_public_render_uses_explicit_runtime_capability_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capability_path = Path(temp) / "runtime.json"
            capability_path.write_text(json.dumps(_capability()), encoding="ascii")
            capability_path.chmod(0o444)
            with mock.patch.object(
                runtime_client, "render_with_runtime", return_value=_result()
            ) as render_runtime:
                rendered = render_residual_preview(
                    _geometry(),
                    _geometry(),
                    capability_path=capability_path,
                )

        render_runtime.assert_called_once()
        self.assertEqual(_capability(), render_runtime.call_args.args[0])
        self.assertEqual(rendered.variant, "step")
        self.assertEqual(len(rendered.views), 8)

    def test_capability_is_read_only_regular_file_with_fixed_loopback_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capability_path = Path(temp) / "runtime.json"
            capability_path.write_text(json.dumps(_capability()), encoding="ascii")
            capability_path.chmod(0o444)
            observed = runtime_client.load_runtime_capability(capability_path)
        self.assertEqual(observed, _capability())

    def test_invalid_or_replaceable_capability_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text(json.dumps(_capability()), encoding="ascii")
            target.chmod(0o444)
            link = root / "runtime.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(MeshshotError, "capability"):
                runtime_client.load_runtime_capability(link)

            invalid = _capability()
            invalid["cadRenderUrl"] = "http://127.0.0.1:not-a-port/cad/render/residual"
            target.chmod(0o644)
            target.write_text(json.dumps(invalid), encoding="ascii")
            target.chmod(0o444)
            with self.assertRaisesRegex(MeshshotError, "identity is invalid"):
                runtime_client.load_runtime_capability(target)

    def test_capability_rejects_tag_and_unregistered_program(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "runtime.json"
            for mutation in (
                {"imageRef": "text-to-cad-browser-runtime:latest"},
                {"programs": {"residual": "sha256:" + "e" * 64}},
            ):
                capability = _capability()
                capability.update(mutation)
                if target.exists():
                    target.chmod(0o600)
                target.write_text(json.dumps(capability), encoding="ascii")
                target.chmod(0o444)
                with self.assertRaisesRegex(MeshshotError, "identity is invalid"):
                    runtime_client.load_runtime_capability(target)

    def test_runtime_request_is_closed_and_identity_bound(self) -> None:
        capability = _capability()
        response_value = {
            "schema": "text-to-cad.cad-render-response/1",
            "jobId": capability["jobId"],
            "program": "residual",
            "programDigest": capability["programs"]["residual"],
            "result": _result(),
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return json.dumps(response_value).encode("ascii")

        _, payload = runtime_client.prepare_payload(
            _geometry(), _geometry(), "step", ()
        )
        with mock.patch.object(
            runtime_client._LOOPBACK_OPENER, "open", return_value=Response()
        ) as open_request:
            observed = runtime_client.render_with_runtime(capability, payload)

        self.assertEqual(observed, response_value["result"])
        request = open_request.call_args.args[0]
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer " + capability["cadRenderToken"],
        )
        self.assertEqual(
            set(json.loads(request.data)),
            {"schema", "jobId", "program", "programDigest", "payload"},
        )
        request_value = json.loads(request.data)
        self.assertEqual(
            request_value["schema"], "text-to-cad.cad-render-request/2"
        )
        packed = request_value["payload"]["reference"]
        self.assertEqual(
            set(packed),
            {
                "schema",
                "vertexCount",
                "faceCount",
                "positionsF32LeBase64",
                "indicesU32LeBase64",
            },
        )
        self.assertEqual(packed["vertexCount"], 3)
        self.assertEqual(packed["faceCount"], 1)
        self.assertEqual(
            struct.unpack(
                "<3I", base64.b64decode(packed["indicesU32LeBase64"])
            ),
            (0, 1, 2),
        )

    def test_loopback_client_does_not_install_environment_proxy_handler(self) -> None:
        proxy_handlers = [
            handler
            for handler in runtime_client._LOOPBACK_OPENER.handlers
            if isinstance(handler, runtime_client.urllib_request.ProxyHandler)
        ]
        self.assertEqual(proxy_handlers, [])

    def test_package_has_no_broker_or_local_browser_adapter(self) -> None:
        package = Path(runtime_client.__file__).resolve().parent
        self.assertFalse((package / "broker_client.py").exists())
        self.assertFalse((package / "browser_contract.json").exists())
        self.assertFalse((package / "renderer.py").exists())
        source = (package / "runtime_client.py").read_text(encoding="utf-8")
        self.assertNotIn("playwright", source)
        self.assertNotIn("browser authority", source.lower())

    def test_capability_ownership_accepts_matching_uid_on_posix(self) -> None:
        # POSIX still requires the loaded file to be owned by the calling
        # user; the helper must round-trip ``os.getuid`` unchanged so the
        # original fail-closed contract in ``load_runtime_capability`` is
        # preserved. ``mock.patch.object(os, "getuid", ...)`` uses
        # ``create=True`` because ``os.getuid`` does not exist on Windows;
        # without it the patch itself raises ``AttributeError`` before
        # the fail-closed helper is invoked, which is precisely the
        # cross-platform-portability regression we are guarding against.
        fake = mock.Mock()
        fake.st_uid = 12345
        with mock.patch.object(runtime_client.os, "getuid", return_value=12345, create=True):
            self.assertTrue(runtime_client._capability_ownership_matches(fake))
        fake.st_uid = 99999
        with mock.patch.object(runtime_client.os, "getuid", return_value=12345, create=True):
            self.assertFalse(runtime_client._capability_ownership_matches(fake))

    def test_capability_ownership_falls_back_when_getuid_is_absent(self) -> None:
        # The regression: Windows does not expose ``os.getuid`` and the
        # previous implementation raised ``AttributeError`` before it
        # could report the real capability status. The helper must not
        # call ``os.getuid`` unconditionally.
        fake = mock.Mock()
        fake.st_uid = 0
        original = runtime_client.os
        try:
            proxy = mock.Mock(spec=[])  # no ``getuid`` attribute
            runtime_client.os = proxy
            self.assertTrue(runtime_client._capability_ownership_matches(fake))
        finally:
            runtime_client.os = original

    @unittest.skipUnless(
        os.name == "nt",
        "Windows-native CreateFileW / FILE_FLAG_OPEN_REPARSE_POINT path; runs on Windows CI",
    )
    def test_windows_load_capability_rejects_symlink_without_o_nofollow(self) -> None:
        # Regression: Windows has no ``O_NOFOLLOW``. Before this fix,
        # ``load_runtime_capability`` on Windows opened via ``os.open``
        # which followed a symlink/reparse point through to its target
        # and returned that target's bytes -- an attacker who could
        # plant a reparse point could substitute the file. The
        # Windows branch now uses ``CreateFileW`` with
        # ``FILE_FLAG_OPEN_REPARSE_POINT`` and rejects any handle
        # whose attributes indicate a reparse point.
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text(json.dumps(_capability()), encoding="ascii")
            target.chmod(0o444)
            link = root / "runtime.json"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(
                    f"cannot create a symlink on this Windows host (SeCreateSymbolicLink required): {exc}"
                )
            with self.assertRaisesRegex(MeshshotError, "capability"):
                runtime_client.load_runtime_capability(link)

    def test_capability_mode_accepts_read_only_on_windows_and_posix(self) -> None:
        posix_ro = mock.Mock(st_mode=0o100444)
        posix_writable = mock.Mock(st_mode=0o100644)
        # POSIX branch: exactly 0o444 is required.
        with mock.patch.object(runtime_client.os, "name", "posix"):
            self.assertTrue(
                runtime_client._capability_mode_is_read_only(posix_ro)
            )
            self.assertFalse(
                runtime_client._capability_mode_is_read_only(posix_writable)
            )
        # Windows branch: exactly 0o444 or any mode with no writable
        # bits set for any user class. Reject a file where the group
        # write bit leaked through.
        with mock.patch.object(runtime_client.os, "name", "nt"):
            self.assertTrue(
                runtime_client._capability_mode_is_read_only(posix_ro)
            )
            windows_ro_variant = mock.Mock(st_mode=0o100400)
            self.assertTrue(
                runtime_client._capability_mode_is_read_only(windows_ro_variant)
            )
            self.assertFalse(
                runtime_client._capability_mode_is_read_only(posix_writable)
            )


if __name__ == "__main__":
    unittest.main()
