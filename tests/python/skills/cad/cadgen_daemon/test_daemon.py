import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import temporary_directory

add_repo_path("skills/cad/scripts")

from cadgen_daemon import client as daemon_client

DAEMON_DIR = REPO_ROOT / "skills" / "cad" / "scripts" / "cadgen_daemon"
STEP_LAUNCHER = REPO_ROOT / "skills" / "cad" / "scripts" / "step"
SPAWN_WAIT_SECONDS = 90.0  # daemon startup pays the full OCP import once

BOX_SOURCE = """\
import build123d


def gen_step():
    return build123d.Box(10.0, 8.0, 4.0)
"""


def _raw_request(sock_path: Path, payload: dict) -> list[dict]:
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(30.0)
    frames: list[dict] = []
    try:
        conn.connect(str(sock_path))
        conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        conn.shutdown(socket.SHUT_WR)
        with conn.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                line = line.strip()
                if line:
                    frames.append(json.loads(line))
    finally:
        conn.close()
    return frames


class CadgenDaemonTests(unittest.TestCase):
    """One daemon serves the whole class; methods are ordered (test_a/b/c) and
    test_c deliberately stops the daemon via the staleness path."""

    server: subprocess.Popen | None = None

    @classmethod
    def setUpClass(cls) -> None:
        # AF_UNIX socket paths are length-limited (~104 bytes on macOS), so the
        # socket gets a short /tmp dir instead of the repo tmp root.
        cls.socket_dir = tempfile.TemporaryDirectory(prefix="cadgen-daemon-", dir="/tmp")
        cls.socket_path = Path(cls.socket_dir.name) / "daemon.sock"
        cls.model_tmp = temporary_directory(prefix="cadgen-daemon-model-")
        cls.model_dir = Path(cls.model_tmp.name)
        (cls.model_dir / "box.step.py").write_text(BOX_SOURCE, encoding="utf-8")

        # Build inline (cold) first so the daemon request is a warm current-skip.
        build_env = {k: v for k, v in os.environ.items() if k != "CADGEN_WARM"}
        build = subprocess.run(
            [sys.executable, str(STEP_LAUNCHER), "box.step.py"],
            cwd=cls.model_dir,
            env=build_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            raise RuntimeError(f"inline warm-up build failed:\n{build.stdout}\n{build.stderr}")

        env = dict(os.environ)
        env["CADGEN_DAEMON_SOCKET"] = str(cls.socket_path)
        env["CADGEN_DAEMON_IDLE_TIMEOUT"] = "300"  # orphan self-cleans if teardown is skipped
        cls.log_path = Path(cls.socket_dir.name) / "daemon.log"
        with open(cls.log_path, "ab") as log_file:
            cls.server = subprocess.Popen(
                [sys.executable, str(DAEMON_DIR / "__main__.py")],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
        deadline = time.monotonic() + SPAWN_WAIT_SECONDS
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError(f"daemon exited during startup:\n{cls.log_path.read_text()}")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(cls.socket_path))
            except OSError:
                time.sleep(0.1)
                continue
            finally:
                probe.close()
            break
        else:
            raise RuntimeError(f"daemon socket never appeared:\n{cls.log_path.read_text()}")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.server is not None and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
        cls.model_tmp.cleanup()
        cls.socket_dir.cleanup()

    def _warm_run(self, argv: list[str]) -> tuple[int | None, str]:
        env = {"CADGEN_WARM": "1", "CADGEN_DAEMON_SOCKET": str(self.socket_path)}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env):
            os.environ.pop("CADGEN_DAEMON_CHILD", None)
            with redirect_stdout(out), redirect_stderr(err):
                exit_code = daemon_client.run_via_daemon("step", argv, cwd=str(self.model_dir))
        return exit_code, out.getvalue() + err.getvalue()

    def test_a_warm_step_request_skips_current_model(self) -> None:
        exit_code, output = self._warm_run(["box.step.py"])
        self.assertEqual(0, exit_code, output)
        self.assertIn("is current", output)

    def test_b_second_request_is_fast_and_correct(self) -> None:
        started = time.perf_counter()
        exit_code, output = self._warm_run(["box.step.py"])
        elapsed = time.perf_counter() - started
        self.assertEqual(0, exit_code, output)
        self.assertIn("is current", output)
        self.assertLess(elapsed, 2.0, f"warm request took {elapsed:.2f}s")

    def test_c_version_token_mismatch_triggers_restart(self) -> None:
        frames = _raw_request(
            self.socket_path,
            {"tool": "step", "argv": ["box.step.py"], "cwd": str(self.model_dir), "token": -1},
        )
        self.assertEqual([{"restart": True}], frames)
        # The server unlinks its socket BEFORE replying, then exits cleanly.
        self.assertFalse(self.socket_path.exists())
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self.server is None or self.server.poll() is not None:
                break
            time.sleep(0.1)
        else:
            self.fail("daemon did not exit after the restart reply")


if __name__ == "__main__":
    unittest.main()
