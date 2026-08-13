"""Linux-only execution proof for the private browser image mount contract."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


try:
    REPO_ROOT = Path(__file__).resolve().parents[4]
except IndexError:
    REPO_ROOT = Path("/")


@unittest.skipUnless(
    sys.platform.startswith("linux")
    and os.environ.get("MESHSHOT_LINUX_EXEC_ROOT_TEST") == "1",
    "requires the controlled Linux noexec-/tmp + exec-root harness",
)
class LinuxPrivateSnapshotExecutionTests(unittest.TestCase):
    def test_private_image_moves_execution_off_noexec_tmp(self) -> None:
        runtime_source = Path(
            os.environ.get(
                "MESHSHOT_BROWSER_RUNTIME_SOURCE",
                REPO_ROOT / "packages/meshshot/src/meshshot/browser_runtime.py",
            )
        )
        package = types.ModuleType("meshshot")
        package.__path__ = [os.fspath(runtime_source.parent)]
        sys.modules.setdefault("meshshot", package)
        runtime = importlib.import_module("meshshot.browser_runtime")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "chrome-headless-shell"
            source.write_text(
                "#!/bin/sh\nprintf 'Google Chrome for Testing 148.0.7778.96\\n'\n",
                encoding="utf-8",
            )
            source.chmod(0o755)
            with self.assertRaises(PermissionError):
                subprocess.run(
                    [source, "--version"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            previous_root = os.environ.get("MESHSHOT_EXECUTABLE_ROOT")
            try:
                os.environ["MESHSHOT_EXECUTABLE_ROOT"] = "/meshshot-exec"
                pinned = runtime._PinnedExecutable(source)
                try:
                    completed = pinned.run_version(timeout=5)
                    self.assertEqual(0, completed.returncode)
                    self.assertEqual(
                        b"Google Chrome for Testing 148.0.7778.96\n",
                        completed.stdout,
                    )
                    self.assertEqual(b"", completed.stderr)
                finally:
                    pinned.close()
            finally:
                if previous_root is None:
                    os.environ.pop("MESHSHOT_EXECUTABLE_ROOT", None)
                else:
                    os.environ["MESHSHOT_EXECUTABLE_ROOT"] = previous_root


@unittest.skipIf(
    os.environ.get("MESHSHOT_LINUX_EXEC_ROOT_TEST") == "1",
    "the controlled Linux container runs only the inner execution proof",
)
class DockerLinuxPrivateSnapshotExecutionTests(unittest.TestCase):
    """Run the Linux proof when a pre-existing local Docker image is available."""

    _IMAGE = "node:22-bookworm"

    def test_local_linux_noexec_harness(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("local Docker is unavailable")
        try:
            daemon = subprocess.run(
                [docker, "info"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            image = subprocess.run(
                [docker, "image", "inspect", self._IMAGE],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("local Docker is unavailable")
        if daemon.returncode != 0 or image.returncode != 0:
            self.skipTest("controlled local Docker image is unavailable")

        name = f"meshshot-linux-exec-{os.getpid()}-{secrets.token_hex(6)}"
        created = False
        try:
            create = subprocess.run(
                [
                    docker,
                    "create",
                    "--pull=never",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "65534:65534",
                    "--tmpfs",
                    "/tmp:noexec,mode=1777,uid=65534,gid=65534",
                    "--tmpfs",
                    "/meshshot-exec:exec,mode=0755,uid=65534,gid=65534",
                    "-e",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "-e",
                    "MESHSHOT_LINUX_EXEC_ROOT_TEST=1",
                    "-e",
                    "MESHSHOT_BROWSER_RUNTIME_SOURCE=/browser_runtime.py",
                    self._IMAGE,
                    "python3",
                    "/test_linux_private_snapshot_exec.py",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            if create.returncode != 0:
                self.skipTest("controlled local Docker container is unavailable")
            created = True
            for source, target in (
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot/browser_runtime.py",
                    "/browser_runtime.py",
                ),
                (Path(__file__), "/test_linux_private_snapshot_exec.py"),
            ):
                copied = subprocess.run(
                    [docker, "cp", os.fspath(source), f"{name}:{target}"],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=15,
                )
                self.assertEqual(0, copied.returncode, "Linux harness copy failed")
            completed = subprocess.run(
                [docker, "start", "--attach", name],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, "Linux harness failed closed")
        finally:
            if created:
                removed = subprocess.run(
                    [docker, "rm", "--force", name],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                self.assertEqual(0, removed.returncode, "Linux harness cleanup failed")


if __name__ == "__main__":
    unittest.main()
