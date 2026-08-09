from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


PUSH_SCRIPT = REPO_ROOT / "scripts" / "pilot" / "cvm-push.sh"
PUSH_MODULE = REPO_ROOT / "scripts" / "pilot" / "cvm_push.py"
PULL_SCRIPT = REPO_ROOT / "scripts" / "pilot" / "cvm-pull.sh"
PUSH_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-push" / "SKILL.md"


class CvmSyncContractTests(unittest.TestCase):
    def test_operation_entrypoints_moved_without_compatibility_wrappers(self) -> None:
        for name in ("cvm-push.sh", "cvm-pull.sh", "snapshot-batch.sh"):
            self.assertTrue((REPO_ROOT / "scripts" / "pilot" / name).is_file())
            self.assertFalse((REPO_ROOT / "scripts" / "utils" / name).exists())
        readme = (REPO_ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        self.assertIn("snapshot, push, submit, monitor, pull", readme)
        self.assertTrue(
            (REPO_ROOT / "tests/python/global/test_cvm_pull.py").is_file()
        )

    def test_push_contract_preserves_remote_state_and_uses_python_workflow(
        self,
    ) -> None:
        ignores = (REPO_ROOT / ".cvmignore").read_text(encoding="utf-8").splitlines()
        wrapper = PUSH_SCRIPT.read_text(encoding="utf-8")
        module = PUSH_MODULE.read_text(encoding="utf-8")
        skill = PUSH_SKILL.read_text(encoding="utf-8")

        self.assertIn(".git/", ignores)
        self.assertIn(".git", ignores)
        self.assertIn("/viewer/", ignores)
        self.assertNotIn("viewer/", ignores)
        self.assertIn(".cvm-jobs/", ignores)
        self.assertNotIn("plugins/", ignores)
        production_lines = "\n".join(
            line
            for line in module.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("--delete", production_lines)
        self.assertNotIn("remove_legacy_plugin_tree", production_lines)
        self.assertNotIn("rm -rf -- plugins", production_lines)
        self.assertNotIn("test ! -e plugins", production_lines)
        self.assertIn('["scripts/bundle/bundle.sh"]', module)
        self.assertIn('exec python3 "$SCRIPT_DIR/cvm_push.py" "$@"', wrapper)
        self.assertIn('"Source: "', module)
        self.assertIn('"Remote Git base: ', module)
        self.assertIn("PRODUCTION_RUNTIME = RuntimeContract", module)
        self.assertIn(
            "CVM production staging failed; no files transferred",
            module,
        )
        self.assertIn("IMPLICIT_NODE_MODULES_INCLUDE", module)
        self.assertIn('["ssh", "-n", "cvm", command]', module)
        self.assertNotIn("prepare_remote_runtime_dirs", module)
        self.assertNotIn("unlink --", module)
        self.assertIn("linked worktree", skill)
        self.assertIn("实体 production bundle", skill)
        self.assertIn("不会修改 source checkout", skill)
        self.assertIn("scripts/pilot/cvm-submit.sh", skill)
        self.assertNotIn(
            "ssh cvm 'cd ~/text-to-cad && ./scripts/pilot/toys4k-pilot.sh",
            skill,
        )
        snapshot_ignores = (REPO_ROOT / ".snapshotignore").read_text(
            encoding="utf-8"
        )
        self.assertIn(".cvm-jobs", snapshot_ignores)

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


if __name__ == "__main__":
    unittest.main()
