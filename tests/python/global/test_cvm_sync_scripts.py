from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


PUSH_SCRIPT = REPO_ROOT / "scripts" / "utils" / "cvm-push.sh"
PULL_SCRIPT = REPO_ROOT / "scripts" / "utils" / "cvm-pull.sh"
PUSH_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-push" / "SKILL.md"
PULL_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-pull" / "SKILL.md"


class CvmSyncContractTests(unittest.TestCase):
    def test_push_supports_checkout_and_linked_worktree_git_shapes(self) -> None:
        ignores = (REPO_ROOT / ".cvmignore").read_text(encoding="utf-8").splitlines()
        script = PUSH_SCRIPT.read_text(encoding="utf-8")
        skill = PUSH_SKILL.read_text(encoding="utf-8")

        self.assertIn(".git/", ignores)
        self.assertIn(".git", ignores)
        self.assertNotIn("--delete", " ".join(
            line for line in script.splitlines() if not line.lstrip().startswith("#")
        ))
        self.assertIn("Source: branch=", script)
        self.assertIn("Remote Git base:", script)
        self.assertIn("linked worktree", skill)
        self.assertIn("scripts/pilot/toys4k-pilot.sh", skill)
        self.assertNotIn("scripts/utils/toys4k-pilot.sh", skill)

    def test_rsync_ignore_preserves_remote_git_dir_for_linked_worktree_source(
        self,
    ) -> None:
        with temporary_directory(prefix="cvm-push-gitfile-") as root_text:
            root = Path(root_text)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (target / ".git").mkdir(parents=True)
            (source / ".git").write_text(
                "gitdir: /tmp/example-worktree\n",
                encoding="utf-8",
            )
            (source / "payload.txt").write_text("new payload\n", encoding="utf-8")
            marker = target / ".git" / "remote-marker"
            marker.write_text("keep\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "rsync",
                    "-a",
                    f"--exclude-from={REPO_ROOT / '.cvmignore'}",
                    f"{source}/",
                    f"{target}/",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(
                (target / "payload.txt").read_text(encoding="utf-8"),
                "new payload\n",
            )

    def test_pull_uses_remote_listing_and_explicit_visibility_status(self) -> None:
        script = PULL_SCRIPT.read_text(encoding="utf-8")
        skill = PULL_SKILL.read_text(encoding="utf-8")

        self.assertNotIn("pgrep -f", script)
        self.assertIn("core/version", script)
        self.assertIn('rclone lsf "$S3_REMOTE"', script)
        self.assertIn("Unsafe CVM exp path", script)
        self.assertIn("preserving CVM postmortem", script)
        self.assertIn("exit 6", script)
        self.assertIn("exit 7", script)
        self.assertIn("?immutable=1", skill)

        parent = 'refresh_dir "ericzyma/text-to-cad/outputs"'
        group = 'refresh_dir "ericzyma/text-to-cad/outputs/$group"'
        exp = 'refresh_dir "ericzyma/text-to-cad/outputs/$exp"'
        self.assertLess(script.index(parent), script.index(group))
        self.assertLess(script.index(group), script.index(exp))

    def test_pull_rejects_parent_traversal_before_upload_or_cleanup(self) -> None:
        with temporary_directory(prefix="cvm-pull-unsafe-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            script_dir = repo / "scripts" / "utils"
            fake_bin = root / "bin"
            script_dir.mkdir(parents=True)
            fake_bin.mkdir()
            shutil.copy2(PULL_SCRIPT, script_dir / "cvm-pull.sh")
            (repo / ".cvmignore.pull").write_text("", encoding="utf-8")

            command_log = root / "commands.log"
            self.write_executable(
                fake_bin / "rclone",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'rclone %s\\n' "$*" >> "$SYNC_TEST_LOG"
[[ "${1:-}" == "rc" ]] && exit 0
printf '%s\\n' 'rclone must not list or refresh after unsafe path' >&2
exit 90
""",
            )
            self.write_executable(
                fake_bin / "ssh",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\\n' "$*" >> "$SYNC_TEST_LOG"
command_text="$*"
case "$command_text" in
  *"find ~/text-to-cad/outputs/"*)
    printf '%s\\n' '20260730-test/..'
    ;;
  *"aws s3 cp"*|*"rm -rf"*)
    printf '%s\\n' 'destructive command must not run' >&2
    exit 91
    ;;
  *)
    printf 'unexpected ssh command: %s\\n' "$command_text" >&2
    exit 92
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "HOME": os.fspath(root / "home"),
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "SYNC_TEST_LOG": os.fspath(command_log),
                    "TMPDIR": os.fspath(root / "tmp"),
                }
            )
            (root / "tmp").mkdir()
            result = subprocess.run(
                [os.fspath(script_dir / "cvm-pull.sh")],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 7)
            self.assertIn("Unsafe CVM exp path", result.stderr)
            commands = command_log.read_text(encoding="utf-8")
            self.assertNotIn("aws s3 cp", commands)
            self.assertNotIn("rm -rf", commands)
            self.assertNotIn("rclone lsf", commands)

    def test_default_pull_preserves_failed_postmortem_without_upload_or_clean(
        self,
    ) -> None:
        with temporary_directory(prefix="cvm-pull-postmortem-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            script_dir = repo / "scripts" / "utils"
            fake_bin = root / "bin"
            script_dir.mkdir(parents=True)
            fake_bin.mkdir()
            shutil.copy2(PULL_SCRIPT, script_dir / "cvm-pull.sh")
            (repo / ".cvmignore.pull").write_text("", encoding="utf-8")

            command_log = root / "commands.log"
            self.write_executable(
                fake_bin / "rclone",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'rclone %s\\n' "$*" >> "$SYNC_TEST_LOG"
case "${1:-}" in
  rc) exit 0 ;;
  lsf) exit 0 ;;
esac
exit 9
""",
            )
            self.write_executable(
                fake_bin / "ssh",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\\n' "$*" >> "$SYNC_TEST_LOG"
command_text="$*"
case "$command_text" in
  *"find ~/text-to-cad/outputs/"*)
    printf '%s\\n' '20260730-test/failed-exp'
    ;;
  *"python3 -c"*)
    printf '%s\\n' '4'
    ;;
  *"test -d ~/text-to-cad/outputs/20260730-test/failed-exp/.codex-upper"*)
    exit 0
    ;;
  *"aws s3 cp"*|*"rm -rf"*)
    printf '%s\\n' 'destructive command must not run' >&2
    exit 90
    ;;
  *)
    printf 'unexpected ssh command: %s\\n' "$command_text" >&2
    exit 91
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "HOME": os.fspath(root / "home"),
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "SYNC_TEST_LOG": os.fspath(command_log),
                    "TMPDIR": os.fspath(root / "tmp"),
                }
            )
            (root / "tmp").mkdir()
            result = subprocess.run(
                [os.fspath(script_dir / "cvm-pull.sh")],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("preserving CVM postmortem", result.stdout)
            self.assertIn("No exp uploaded", result.stdout)
            commands = command_log.read_text(encoding="utf-8")
            self.assertNotIn("aws s3 cp", commands)
            self.assertNotIn("rm -rf", commands)

    def test_successful_pull_verifies_cleans_and_refreshes_parent_first(
        self,
    ) -> None:
        with temporary_directory(prefix="cvm-pull-success-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            script_dir = repo / "scripts" / "utils"
            fake_bin = root / "bin"
            script_dir.mkdir(parents=True)
            fake_bin.mkdir()
            shutil.copy2(PULL_SCRIPT, script_dir / "cvm-pull.sh")
            (repo / ".cvmignore.pull").write_text(
                "stderr.log\n.codex-upper/*\n",
                encoding="utf-8",
            )

            command_log = root / "commands.log"
            self.write_executable(
                fake_bin / "rclone",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'rclone %s\\n' "$*" >> "$SYNC_TEST_LOG"
case "${1:-}" in
  lsf)
    exit 0
    ;;
  rc)
    if [[ "$*" == *"vfs/refresh"*"/20260730-test/success-exp"* ]]; then
      mkdir -p "$HOME/threed-code/ericzyma/text-to-cad/outputs/20260730-test/success-exp"
    fi
    exit 0
    ;;
esac
exit 9
""",
            )
            self.write_executable(
                fake_bin / "ssh",
                """#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\\n' "$*" >> "$SYNC_TEST_LOG"
command_text="$*"
case "$command_text" in
  *"find ~/text-to-cad/outputs/"*"-mindepth 2"*)
    printf '%s\\n' '20260730-test/success-exp'
    ;;
  *"python3 -c"*)
    printf '%s\\n' '0'
    ;;
  *"test -d ~/text-to-cad/outputs/20260730-test/success-exp/.codex-upper"*)
    exit 1
    ;;
  *"aws s3 cp"*)
    printf '%s\\n' 'upload complete'
    ;;
  *"find ~/text-to-cad/outputs/20260730-test/success-exp/"*"-type f"*)
    printf '%s\\n' '2'
    ;;
  *"aws s3 ls --recursive"*)
    printf '%s\\n' '2'
    ;;
  *"rm -rf -- ~/text-to-cad/outputs/20260730-test/success-exp"*)
    exit 0
    ;;
  *)
    printf 'unexpected ssh command: %s\\n' "$command_text" >&2
    exit 91
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "HOME": os.fspath(root / "home"),
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "SYNC_TEST_LOG": os.fspath(command_log),
                    "TMPDIR": os.fspath(root / "tmp"),
                }
            )
            (root / "tmp").mkdir()
            result = subprocess.run(
                [os.fspath(script_dir / "cvm-pull.sh")],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("verify OK (2 files)", result.stdout)
            self.assertIn("mount-visible", result.stdout)
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn("aws s3 cp", commands)
            self.assertIn("rm -rf --", commands)
            parent = "dir=ericzyma/text-to-cad/outputs recursive=false"
            group = "dir=ericzyma/text-to-cad/outputs/20260730-test recursive=false"
            exp = (
                "dir=ericzyma/text-to-cad/outputs/"
                "20260730-test/success-exp recursive=false"
            )
            self.assertLess(commands.index(parent), commands.index(group))
            self.assertLess(commands.index(group), commands.index(exp))

    @staticmethod
    def write_executable(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
