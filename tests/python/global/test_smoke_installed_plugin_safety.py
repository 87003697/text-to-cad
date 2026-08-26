"""Regression coverage for the installed-plugin smoke cleanup boundary."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE = REPO_ROOT / "scripts/release/smoke-installed-plugin.sh"
ANCHOR_ROOT = Path(
    "/Users/zhiyuanma/Desktop/桌面 - ERICZYMA-MC0/codes/text-to-cad"
)


def _physical_git_root(path: Path) -> Path:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _identity(path: Path) -> tuple[int, int, str, str]:
    git_path = path / ".git"
    git_head = subprocess.run(
        ["/usr/bin/git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return (path.stat().st_ino, git_path.stat().st_ino, git_head, path.name)


class InstalledPluginSmokeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(REPO_ROOT.is_dir())
        self.assertTrue(ANCHOR_ROOT.is_dir())
        self.assertTrue(SMOKE.is_file())
        self.source_physical = _physical_git_root(REPO_ROOT)
        self.anchor_before = _identity(ANCHOR_ROOT)
        self.source_before = _identity(self.source_physical)

    def _run_smoke(self, fake_bin: Path, temp_root: Path, *, confuse: bool) -> subprocess.CompletedProcess[str]:
        rm_log = temp_root / "rm.log"
        receipt = temp_root / "receipt.json"
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
        env["SMOKE_RM_LOG"] = str(rm_log)
        env["SMOKE_SOURCE_ROOT"] = str(self.source_physical)
        env["SMOKE_SOURCE_ROOT_ALT"] = str(REPO_ROOT)
        env["SMOKE_ANCHOR_ROOT"] = str(ANCHOR_ROOT)
        env["SMOKE_CONFUSED_ROOT"] = str(self.source_physical)
        env["SMOKE_PRESERVE_WORKTREE"] = "1"
        if confuse:
            env["SMOKE_CONFUSE_MKTEMP"] = "1"
        else:
            env.pop("SMOKE_CONFUSE_MKTEMP", None)
        python = REPO_ROOT / ".venv/bin/python"
        if not python.is_file():
            python = Path(sys.executable)
        return subprocess.run(
            [
                str(SMOKE),
                "--receipt",
                str(receipt),
                "--codex",
                "/usr/bin/true",
                "--python",
                str(python),
            ],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _fake_bin(root: Path) -> Path:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (fake_bin / "git").write_text(
            "#!/bin/sh\n"
            "case \" $* \" in\n"
            "  *' worktree add '*|*' worktree remove '*) exit 23 ;;\n"
            "  *) exec /usr/bin/git \"$@\" ;;\n"
            "esac\n"
        )
        (fake_bin / "mktemp").write_text(
            "#!/bin/sh\n"
            "last=\"\"\n"
            "for arg do last=\"$arg\"; done\n"
            "case \"$last\" in\n"
            "  *installed-plugin-smoke-prep*)\n"
            "    if [ \"${SMOKE_CONFUSE_MKTEMP:-}\" = 1 ]; then\n"
            "      printf '%s\\n' \"$SMOKE_CONFUSED_ROOT\"\n"
            "      exit 0\n"
            "    fi\n"
            "    ;;\n"
            "esac\n"
            "exec /usr/bin/mktemp \"$@\"\n"
        )
        (fake_bin / "rm").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$SMOKE_RM_LOG\"\n"
            "for arg do\n"
            "  case \"$arg\" in -*) continue ;; esac\n"
            "  case \"$arg\" in\n"
            "    \"$SMOKE_SOURCE_ROOT\"|\"$SMOKE_SOURCE_ROOT_ALT\"|\"$SMOKE_ANCHOR_ROOT\")\n"
            "      printf 'DANGEROUS:%s\\n' \"$arg\" >> \"$SMOKE_RM_LOG\"\n"
            "      exit 97\n"
            "      ;;\n"
            "  esac\n"
            "  case \"$arg\" in\n"
            "    /private/tmp/installed-plugin-smoke-prep-*)\n"
            "      [ \"${SMOKE_PRESERVE_WORKTREE:-}\" = 1 ] && exit 0\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            "exec /bin/rm \"$@\"\n"
        )
        for executable in ("git", "mktemp", "rm"):
            (fake_bin / executable).chmod(0o755)
        return fake_bin

    def test_path_confusion_fails_closed_before_recursive_delete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-safety-confused-") as text:
            root = Path(text)
            completed = self._run_smoke(self._fake_bin(root), root, confuse=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unsafe smoke temp path", completed.stderr)
            rm_log = (root / "rm.log").read_text() if (root / "rm.log").exists() else ""
            self.assertNotIn("DANGEROUS:", rm_log)
            self.assertEqual(self.source_before, _identity(self.source_physical))
            self.assertEqual(self.anchor_before, _identity(ANCHOR_ROOT))

    def test_unregistered_worktree_is_not_removed_by_recursive_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-safety-cleanup-") as text:
            root = Path(text)
            completed = self._run_smoke(self._fake_bin(root), root, confuse=False)
            self.assertNotEqual(completed.returncode, 0)
            rm_lines = (
                (root / "rm.log").read_text().splitlines()
                if (root / "rm.log").exists()
                else []
            )
            self.assertEqual(len(rm_lines), 1)
            self.assertNotIn("DANGEROUS:", "\n".join(rm_lines))
            self.assertEqual(self.source_before, _identity(self.source_physical))
            self.assertEqual(self.anchor_before, _identity(ANCHOR_ROOT))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
