"""Linux-only execution proof for the private browser image mount contract."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
