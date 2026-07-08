"""HTTP-level tests for GET /__cad/integrations, POST /__cad/open-in, and
POST /__cad/reveal — a real ThreadingHTTPServer on an ephemeral loopback port,
with app launching disabled via the integrations env seams."""

import http.client
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import backend as backend_mod  # noqa: E402
from server_py import server as server_mod  # noqa: E402

_ENV_KEYS = (
    "VIEWER_OPEN_IN_PLATFORM",
    "VIEWER_OPEN_IN_FORCE_INSTALLED",
    "VIEWER_DISABLE_APP_LAUNCH",
    "VIEWER_ALLOWED_HOSTS",
)


class OpenInHttpRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls._tmp.name)
        cls.step_path = os.path.join(cls.root, "part.step")
        with open(cls.step_path, "w", encoding="utf-8") as handle:
            handle.write("ISO-10303-21;\nEND-ISO-10303-21;\n")
        server_mod._Ctx.directory_root = cls.root
        server_mod._Ctx.dist_root = os.path.join(cls.root, "dist")
        server_mod._Ctx.backend = backend_mod.LocalAssetBackend(directory_root=cls.root, root_dir=cls.root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        server_mod._Ctx.port = cls.httpd.server_address[1]
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["VIEWER_DISABLE_APP_LAUNCH"] = "1"
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = "freecad=/Applications/FreeCAD.app"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
        finally:
            conn.close()
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            payload = None
        return response.status, payload

    def _open_in_path(self, file_path=None, app="freecad"):
        from urllib.parse import quote
        return f"/__cad/open-in?file={quote(file_path or self.step_path, safe='')}&app={quote(app, safe='')}"

    def test_get_integrations_lists_targets(self):
        status, payload = self._request("GET", "/__cad/integrations")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["platform"], "darwin")
        targets = {t["id"]: t for t in payload["targets"]}
        self.assertTrue(targets["freecad"]["installed"])
        self.assertTrue(targets["system-default"]["installed"])

    def test_open_in_launches_allowlisted_app(self):
        status, payload = self._request("POST", self._open_in_path())
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["appId"], "freecad")
        self.assertEqual(payload["filename"], "part.step")
        self.assertFalse(payload["materialized"])
        self.assertEqual(payload["command"][0], "/Applications/FreeCAD.app/Contents/MacOS/FreeCAD")

    def test_open_in_rejects_foreign_origin(self):
        status, payload = self._request(
            "POST", self._open_in_path(), headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertFalse(payload["ok"])

    def test_open_in_rejects_null_origin(self):
        status, _payload = self._request(
            "POST", self._open_in_path(), headers={"Origin": "null"})
        self.assertEqual(status, 403)

    def test_open_in_rejects_foreign_host(self):
        # DNS-rebinding shape: the request reaches loopback but Host is a public name.
        status, _payload = self._request(
            "POST", self._open_in_path(), headers={"Host": "evil.example:443"})
        self.assertEqual(status, 403)

    def test_open_in_allows_loopback_origin_on_other_port(self):
        # vite dev mode: the browser origin carries the vite port, not this server's.
        status, payload = self._request(
            "POST", self._open_in_path(), headers={"Origin": "http://127.0.0.1:5173"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_open_in_rejects_unknown_app(self):
        status, payload = self._request("POST", self._open_in_path(app="netcat"))
        self.assertEqual(status, 400)
        self.assertIn("Unknown", payload["error"])

    def test_open_in_rejects_file_outside_root(self):
        status, payload = self._request("POST", self._open_in_path(file_path="/etc/hosts"))
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_open_in_rejects_lfs_pointer(self):
        pointer = os.path.join(self.root, "pointer.step")
        with open(pointer, "w", encoding="utf-8") as handle:
            handle.write("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 42\n")
        status, payload = self._request("POST", self._open_in_path(file_path=pointer))
        self.assertEqual(status, 400)
        self.assertIn("git lfs checkout", payload["error"])

    def test_reveal_selects_entry_file(self):
        from urllib.parse import quote
        status, payload = self._request(
            "POST", f"/__cad/reveal?file={quote(self.step_path, safe='')}&asset=output")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], ["open", "-R", self.step_path])

    def test_reveal_missing_file_is_an_error(self):
        from urllib.parse import quote
        missing = os.path.join(self.root, "absent.step")
        status, payload = self._request(
            "POST", f"/__cad/reveal?file={quote(missing, safe='')}&asset=output")
        self.assertEqual(status, 400)
        self.assertIn("not found", payload["error"].lower())

    def test_reveal_outside_root_forbidden(self):
        from urllib.parse import quote
        status, _payload = self._request(
            "POST", f"/__cad/reveal?file={quote('/etc/hosts', safe='')}&asset=output")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
