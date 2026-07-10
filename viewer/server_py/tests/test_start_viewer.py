"""Tests for the single-port CAD Viewer launcher (server_py.start_viewer).

Cover the two branches (start when the port is free / error when it is occupied),
--dir validation, port selection, and the --json contract, with the port probe
and backend spawn stubbed so no network or child process is touched.
"""

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import start_viewer as sav  # noqa: E402


class _FakeChild:
    def __init__(self, code=0):
        self._code = code

    def wait(self):
        return self._code


@contextlib.contextmanager
def _run(argv, port_free=True, child_code=0, cad_backend_error=""):
    """Run main() with the port probe + backend spawn stubbed; yield
    (rc, out, err, calls)."""
    calls = {"spawn": []}

    def fake_spawn(host, port, directory):
        calls["spawn"].append((host, port, directory))
        return _FakeChild(child_code)

    out, err = io.StringIO(), io.StringIO()
    probe_effect = RuntimeError(cad_backend_error) if cad_backend_error else None
    with mock.patch.object(sav, "port_is_free", return_value=port_free), \
            mock.patch.object(sav, "spawn_backend", side_effect=fake_spawn), \
            mock.patch.object(sav.cadgen_bridge, "require_cadgen_runtime", side_effect=probe_effect), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = sav.main(argv)
    yield rc, out.getvalue(), err.getvalue(), calls


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_starts_when_port_free(self):
        with _run(["--dir", self.directory], port_free=True) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            self.assertIn("CAD Viewer URL:", out)
            self.assertEqual(len(calls["spawn"]), 1)
            # default port is 3245
            self.assertEqual(calls["spawn"][0][1], sav.DEFAULT_VIEWER_PORT)

    def test_occupied_errors_without_rolling(self):
        with _run(["--dir", self.directory, "--port", "3245"], port_free=False) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertIn("3245", err)
            self.assertIn("--port", err)
            self.assertEqual(calls["spawn"], [])

    def test_missing_dir_errors(self):
        missing = os.path.join(self.directory, "does-not-exist")
        with _run(["--dir", missing], port_free=True) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertEqual(calls["spawn"], [])

    def test_custom_port_is_used(self):
        with _run(["--dir", self.directory, "--port", "4321"], port_free=True) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            self.assertEqual(calls["spawn"][0][1], 4321)

    def test_start_propagates_child_exit_code(self):
        with _run(["--dir", self.directory], port_free=True, child_code=3) as (rc, out, err, calls):
            self.assertEqual(rc, 3)

    def test_invalid_cad_backend_refuses_to_start(self):
        with _run(
            ["--dir", self.directory],
            port_free=True,
            cad_backend_error="No module named 'OCP'",
        ) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertEqual(calls["spawn"], [])
            self.assertNotIn("CAD Viewer URL:", out)
            self.assertIn("No module named 'OCP'", err)

    def test_json_output_start(self):
        with _run(["--dir", self.directory, "--json"], port_free=True) as (rc, out, err, calls):
            payload = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
            self.assertEqual(payload["action"], "start")
            self.assertEqual(payload["port"], sav.DEFAULT_VIEWER_PORT)
            self.assertTrue(payload["url"].startswith("http://"))


if __name__ == "__main__":
    unittest.main()
