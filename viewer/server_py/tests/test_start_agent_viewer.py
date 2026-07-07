"""Tests for the fixed-port CAD Viewer launcher (server_py.start_agent_viewer).

Cover the three single-port branches (reuse / start / occupied), the reuse gate,
and the --json contract, with probe/activate/spawn stubbed so no network or
child process is touched.
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

from server_py import start_agent_viewer as sav  # noqa: E402

_REUSABLE_INFO = {
    "app": "cad-viewer",
    "dynamicRoot": True,
    "serverFeatures": ["dynamic-root", "directory-activation"],
}


class _FakeChild:
    def __init__(self, code=0):
        self._code = code

    def wait(self):
        return self._code


@contextlib.contextmanager
def _run(argv, probe_result, child_code=0):
    """Run main() with probe/activate/spawn stubbed; yield (rc, out, err, calls)."""
    calls = {"activate": [], "spawn": []}

    def fake_activate(host, port, directory):
        calls["activate"].append((host, port, directory))

    def fake_spawn(mode, host, port, directory):
        calls["spawn"].append((mode, host, port, directory))
        return _FakeChild(child_code)

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sav, "probe", return_value=probe_result), \
            mock.patch.object(sav, "activate_directory", side_effect=fake_activate), \
            mock.patch.object(sav, "spawn_backend", side_effect=fake_spawn), \
            mock.patch.object(sav, "select_mode", return_value="serve"), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = sav.main(argv)
    yield rc, out.getvalue(), err.getvalue(), calls


class IsReusableTest(unittest.TestCase):
    def test_accepts_valid_info(self):
        self.assertTrue(sav.is_reusable(_REUSABLE_INFO))

    def test_rejects_missing_feature(self):
        info = {**_REUSABLE_INFO, "serverFeatures": ["dynamic-root"]}
        self.assertFalse(sav.is_reusable(info))

    def test_rejects_wrong_app(self):
        self.assertFalse(sav.is_reusable({**_REUSABLE_INFO, "app": "something-else"}))

    def test_rejects_non_dynamic_root(self):
        self.assertFalse(sav.is_reusable({**_REUSABLE_INFO, "dynamicRoot": False}))

    def test_rejects_non_dict(self):
        self.assertFalse(sav.is_reusable(None))


class LauncherBranchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_reuse_branch(self):
        argv = ["--host", "127.0.0.1", "--dir", self.directory]
        with _run(argv, ("viewer", _REUSABLE_INFO)) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            self.assertIn("already running", out)
            self.assertIn("CAD Viewer URL:", out)
            self.assertEqual(len(calls["activate"]), 1)
            self.assertEqual(calls["spawn"], [])

    def test_start_branch_when_port_closed(self):
        argv = ["--dir", self.directory]
        with _run(argv, ("closed", None)) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            self.assertIn("Starting CAD Viewer serve server", out)
            self.assertEqual(len(calls["spawn"]), 1)
            self.assertEqual(calls["activate"], [])
            # default port is 4178
            self.assertEqual(calls["spawn"][0][2], sav.DEFAULT_VIEWER_PORT)

    def test_start_branch_propagates_child_exit_code(self):
        argv = ["--dir", self.directory]
        with _run(argv, ("closed", None), child_code=3) as (rc, out, err, calls):
            self.assertEqual(rc, 3)

    def test_occupied_branch_errors_without_rolling(self):
        argv = ["--dir", self.directory, "--port", "4178"]
        with _run(argv, ("occupied", None)) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertIn("4178", err)
            self.assertIn("--port", err)
            self.assertEqual(calls["spawn"], [])
            self.assertEqual(calls["activate"], [])

    def test_running_viewer_that_fails_gate_is_not_reused(self):
        stale = {"app": "cad-viewer", "dynamicRoot": True, "serverFeatures": []}
        argv = ["--dir", self.directory]
        with _run(argv, ("viewer", stale)) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertEqual(calls["activate"], [])
            self.assertEqual(calls["spawn"], [])

    def test_missing_dir_errors(self):
        missing = os.path.join(self.directory, "does-not-exist")
        with _run(["--dir", missing], ("closed", None)) as (rc, out, err, calls):
            self.assertEqual(rc, 1)
            self.assertEqual(calls["spawn"], [])

    def test_custom_port_is_used(self):
        argv = ["--dir", self.directory, "--port", "4321"]
        with _run(argv, ("closed", None)) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            self.assertEqual(calls["spawn"][0][2], 4321)

    def test_json_output_reuse(self):
        argv = ["--dir", self.directory, "--json"]
        with _run(argv, ("viewer", _REUSABLE_INFO)) as (rc, out, err, calls):
            self.assertEqual(rc, 0)
            payload = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
            self.assertEqual(payload["action"], "reuse")
            self.assertEqual(payload["port"], sav.DEFAULT_VIEWER_PORT)
            self.assertTrue(payload["url"].startswith("http://"))

    def test_json_output_start(self):
        argv = ["--dir", self.directory, "--json"]
        with _run(argv, ("closed", None)) as (rc, out, err, calls):
            payload = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
            self.assertEqual(payload["action"], "start")


if __name__ == "__main__":
    unittest.main()
