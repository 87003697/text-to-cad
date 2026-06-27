"""End-to-end HTTP parity capstone: boot the Node and Python servers on the same
models dir and diff the live /__cad responses. Skips if Node or the cadjs
workspace symlinks are unavailable (lightweight worktree / CI without a dev layout).
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

_WORKTREE = pathlib.Path(__file__).resolve().parents[3]
_MODELS = _WORKTREE / "models"
_PYVENV = pathlib.Path("/Users/jakefitzgerald/robots/text-to-cad/.venv/bin/python")
_PY = str(_PYVENV) if _PYVENV.exists() else sys.executable


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


def _wait_up(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _get(port, "/__cad/server")
            return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.3)
    return False


class HttpParity(unittest.TestCase):
    node = None
    py = None
    node_port = 0
    py_port = 0

    @classmethod
    def setUpClass(cls):
        if not _MODELS.is_dir():
            raise unittest.SkipTest("models/ not hydrated")
        cls.node_port = _free_port()
        cls.py_port = _free_port()
        node_env = dict(os.environ, VIEWER_SERVER_REGISTRY="/tmp/_parity_node_reg.json")
        py_env = dict(os.environ, PYTHONPATH=str(_WORKTREE / "viewer"), VIEWER_SERVER_REGISTRY="/tmp/_parity_py_reg.json")
        try:
            cls.node = subprocess.Popen(
                ["node", "src/server/server.mjs", "--dir", str(_WORKTREE), "--port", str(cls.node_port)],
                cwd=str(_WORKTREE / "viewer"), env=node_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            cls.py = subprocess.Popen(
                [_PY, "-m", "server_py.server", "--dir", str(_WORKTREE), "--port", str(cls.py_port)],
                cwd=str(_WORKTREE), env=py_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise unittest.SkipTest(f"cannot launch servers: {exc}")
        if not (_wait_up(cls.node_port) and _wait_up(cls.py_port)):
            cls.tearDownClass()
            raise unittest.SkipTest("Node (cadjs symlinks) or Python server did not start")

    @classmethod
    def tearDownClass(cls):
        for proc in (cls.node, cls.py):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _diff(self, path):
        return _get(self.node_port, path), _get(self.py_port, path)

    def test_server_info_stable_keys(self):
        n, p = self._diff(f"/__cad/server?dir={_MODELS}/gcode")
        for key in ("schemaVersion", "serverApiVersion", "app", "backend", "dynamicRoot", "rootName", "serverFeatures", "stepArtifactGenerationAvailable"):
            self.assertEqual(n.get(key), p.get(key), key)

    def test_catalog_identical(self):
        for sub in ("gcode", "implicits", "robots/tom", "robots/lyra"):
            if not (_MODELS / sub).is_dir():
                continue
            n, p = self._diff(f"/__cad/catalog?dir={_MODELS}/{sub}")
            self.assertEqual(n, p, f"catalog mismatch for {sub}")


if __name__ == "__main__":
    unittest.main()
