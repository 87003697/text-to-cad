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
import random
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
    # LOW range, deliberately not the kernel's ephemeral one: Windows NAT drivers
    # reserve blocks of the ephemeral range, where a connect() to a CLOSED port can
    # fail without a refusal -- which start_viewer's occupancy probe must treat as
    # occupied. Ports down here behave on every platform.
    for _attempt in range(20):
        port = random.randint(20000, 39999)
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise AssertionError("no bindable port found in the low range")


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


class ViewerExitedError(AssertionError):
    """The packaged start command exited before serving; carries its output."""

    def __init__(self, output: str) -> None:
        super().__init__(
            f"the viewer exited before serving (code shown in output): {output[-2000:]}"
        )
        self.output = output


def _drain(proc: subprocess.Popen) -> str:
    try:
        output, _ = proc.communicate(timeout=5)
        return output or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


@unittest.skipUnless(_node_available(), "node is not available")


@unittest.skipUnless(
    _node_available() and _npm_command() is not None,
    "node/npm are not available",
)
class PackagedViewerStartSmokeTests(unittest.TestCase):
    """Live: `npm run start` boots the backend and answers /__cad/server."""

    def test_start_command_boots_the_backend_on_the_requested_port(self):
        # Ephemeral-port probes can be re-grabbed between our close() and the
        # viewer's bind (observed on Windows), so a port reported busy is RETIRED,
        # not failed -- up to a few candidates.
        last_failure = ""
        for _attempt in range(4):
            port = _free_port()
            proc = self._spawn_viewer(port)
            try:
                self._assert_server_ready(proc, port)
                return
            except ViewerExitedError as exc:
                if "already in use" not in exc.output:
                    raise
                last_failure = exc.output
            finally:
                self._stop_tree(proc)
                proc.wait(timeout=30)
        self.fail(f"every candidate port was already in use: {last_failure}")

    def _spawn_viewer(self, port: int) -> subprocess.Popen:
        env = dict(os.environ)
        # The shim intentionally serves the CALLER's cwd as the default directory;
        # point it at a throwaway dir so the smoke never depends on where pytest/unittest ran.
        env["INIT_CWD"] = str(pathlib.Path(tempfile.gettempdir()))
        # This smoke boots the SERVE surface, not the CAD build toolchain: skip the
        # cadgen-runtime probe the launcher otherwise runs (the same escape hatch
        # test_server_startup uses), so the suite passes wherever node exists.
        env["VIEWER_CAD_BACKEND_VALIDATED"] = "1"
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        return subprocess.Popen(
            [*_npm_command(), "--prefix", str(VIEWER_APP), "run", "start", "--",
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            **popen_kwargs,
        )

    def _assert_server_ready(self, proc: subprocess.Popen, port: int) -> None:
        url = f"http://127.0.0.1:{port}/__cad/server"
        deadline = time.monotonic() + 45
        last_error = ""
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ViewerExitedError(_drain(proc))
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
