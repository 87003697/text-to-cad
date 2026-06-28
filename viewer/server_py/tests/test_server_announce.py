"""The ephemeral-port + URL-announce handshake the desktop (Tauri) shell relies on.

`server.py --port 0 --announce-url` must bind a free loopback port and print a
single machine-readable `CAD_VIEWER_URL=<url>` line on stdout that a supervising
parent reads to discover where to point the webview. OCP-free: hitting
`/__cad/server` does not scan the catalog.
"""

import json
import os
import pathlib
import subprocess
import sys
import time
import unittest
import urllib.request

_WORKTREE = pathlib.Path(__file__).resolve().parents[3]
_VIEWER = _WORKTREE / "viewer"


class ServerAnnounce(unittest.TestCase):
    def test_ephemeral_port_announce_and_serve(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_VIEWER), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        proc = subprocess.Popen(
            [sys.executable, "-m", "server_py.server",
             "--dir", str(_WORKTREE / "models"),
             "--host", "127.0.0.1", "--port", "0", "--announce-url"],
            cwd=str(_WORKTREE), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            url = None
            deadline = time.time() + 15
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line == "" and proc.poll() is not None:
                    break
                prefix = "CAD_VIEWER_URL="
                if line.startswith(prefix):
                    url = line.strip()[len(prefix):]
                    break
            self.assertIsNotNone(url, "server did not announce CAD_VIEWER_URL")
            self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/$")
            self.assertNotIn(":0/", url)  # an ACTUAL ephemeral port, not 0

            with urllib.request.urlopen(url + "__cad/server", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                info = json.loads(resp.read())
            self.assertEqual(info.get("app"), "cad-viewer")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.stdout:
                proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
