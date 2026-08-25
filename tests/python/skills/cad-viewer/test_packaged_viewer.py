"""Packaged-runtime smoke tests for the cad-viewer skill.

The cad-viewer skill was the only one without any test suite. These pin the
contract an agent depends on: the vendored runtime layout, the documented start
command, and -- live -- that `npm run start` actually boots the Python backend
and answers the /__cad/server health route on the requested port.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import pathlib
import socket
import subprocess
import sys
import time
import unittest
import urllib.request

from tests.python.support.paths import repo_path

VIEWER_SKILL = repo_path("skills", "cad-viewer")
VIEWER_APP = VIEWER_SKILL / "scripts" / "viewer"


def _node_available() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=15)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _npm_command() -> list[str] | None:
    """The npm launcher as an executable list, or None when npm is unavailable.

    Resolved through PATH because Windows cannot spawn `npm` bare: CreateProcess
    does not try the .cmd shim."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    return [npm] if npm else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PackagedViewerLayoutTests(unittest.TestCase):
    """The static contract: what must exist for the start command to work at all."""

    def test_the_vendored_viewer_runtime_is_present(self):
        self.assertTrue(VIEWER_APP.is_dir(), "skills/cad-viewer/scripts/viewer must resolve")
        self.assertTrue((VIEWER_APP / "package.json").is_file())
        self.assertTrue(
            (VIEWER_APP / "scripts" / "start-viewer.mjs").is_file(),
            "the npm start shim must exist",
        )
        self.assertTrue(
            (VIEWER_APP / "server_py" / "start_viewer.py").is_file(),
            "the Python launcher behind the shim must ship",
        )

    def test_package_json_defines_the_start_command(self):
        package = json.loads((VIEWER_APP / "package.json").read_text(encoding="utf-8"))
        self.assertIn("start", package.get("scripts", {}))

    def test_skill_md_documents_the_start_command_and_default_port(self):
        skill_md = (VIEWER_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("npm --prefix scripts/viewer run start", skill_md)
        self.assertIn("3245", skill_md)

    def test_requirements_pin_the_vendored_cadgen(self):
        requirements = (VIEWER_SKILL / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("./scripts/viewer/packages/cadgen", requirements)


@unittest.skipUnless(
    _node_available() and _npm_command() is not None,
    "node/npm are not available",
)
class PackagedViewerStartSmokeTests(unittest.TestCase):
    """Live: `npm run start` boots the backend and answers /__cad/server."""

    def test_start_command_boots_the_backend_on_the_requested_port(self):
        port = _free_port()
        env = dict(os.environ)
        # The shim intentionally serves the CALLER's cwd as the default directory;
        # point it at a throwaway dir so the smoke never depends on where pytest/unittest ran.
        env["INIT_CWD"] = str(pathlib.Path(tempfile.gettempdir()))
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [*_npm_command(), "--prefix", str(VIEWER_APP), "run", "start", "--",
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            **popen_kwargs,
        )
        try:
            self._assert_server_ready(proc, port)
        finally:
            self._stop_tree(proc)
        # The shim forwards the launcher's exit code; a clean terminate is fine either way.
        proc.wait(timeout=30)

    def _assert_server_ready(self, proc: subprocess.Popen, port: int) -> None:
        url = f"http://127.0.0.1:{port}/__cad/server"
        deadline = time.monotonic() + 45
        last_error = ""
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.fail("the viewer exited before serving; run npm --prefix viewer install/build")
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload.get("backend"), "local-fs")
                self.assertEqual(int(payload.get("port") or 0), port)
                return
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.25)
        self.fail(f"the viewer never answered {url}: {last_error}")

    def _stop_tree(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            with contextlib.suppress(OSError):
                proc.kill()
        with contextlib.suppress(OSError, ValueError):
            if proc.stdout:
                proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
