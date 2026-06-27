"""Unit tests for the save-dialog env hooks and the cadpy subprocess bridge."""

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import cadpy_bridge, save_dialog  # noqa: E402

_WORKTREE = pathlib.Path(__file__).resolve().parents[3]


class SaveDialogEnvHooks(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("VIEWER_SAVE_DIALOG_FORCE_PATH", "VIEWER_DISABLE_NATIVE_SAVE_DIALOG")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_forced_path(self):
        os.environ["VIEWER_SAVE_DIALOG_FORCE_PATH"] = "/tmp/out.stl"
        self.assertEqual(save_dialog.pick_save_destination(), {"path": "/tmp/out.stl"})

    def test_forced_cancel(self):
        os.environ["VIEWER_SAVE_DIALOG_FORCE_PATH"] = "__cancel__"
        self.assertEqual(save_dialog.pick_save_destination(), {"cancelled": True})

    def test_disabled_is_unsupported(self):
        os.environ["VIEWER_DISABLE_NATIVE_SAVE_DIALOG"] = "1"
        self.assertEqual(save_dialog.pick_save_destination(), {"unsupported": True})


class CadpyPythonpath(unittest.TestCase):
    def test_discovers_cadpy_src(self):
        if not (_WORKTREE / "packages" / "cadpy" / "src").is_dir():
            self.skipTest("packages/cadpy/src not present")
        pp = cadpy_bridge.cadpy_pythonpath(str(_WORKTREE))
        self.assertIn(os.path.join("packages", "cadpy", "src"), pp)


if __name__ == "__main__":
    unittest.main()
