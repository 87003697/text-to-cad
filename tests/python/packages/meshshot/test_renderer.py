"""Contract tests for the one Browser Runtime meshshot client."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
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
        "programs": {"residual": runtime_client.EXPECTED_RESIDUAL_PROGRAM},
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


if __name__ == "__main__":
    unittest.main()
