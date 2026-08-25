from __future__ import annotations

import fnmatch
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT


MODULE_PATH = REPO_ROOT / "scripts" / "pilot" / "cvm_pull.py"
SPEC = importlib.util.spec_from_file_location("cvm_pull", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cvm_pull = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cvm_pull
SPEC.loader.exec_module(cvm_pull)


class FakeRunner:
    def __init__(self) -> None:
        self.local: list[tuple[str, ...]] = []
        self.remote_commands: list[str] = []
        self.upload_status = 0
        self.responses: list[tuple[str, int, str]] = []

    def respond(self, marker: str, stdout: str = "", status: int = 0) -> None:
        self.responses.append((marker, status, stdout))

    def run(self, argv, *, check=True, capture=True):
        self.local.append(tuple(argv))
        return mock.Mock(returncode=0, stdout="", stderr="")

    def remote(self, command: str, *, check: bool = True):
        self.remote_commands.append(command)
        for marker, status, stdout in self.responses:
            if marker in command:
                return mock.Mock(returncode=status, stdout=stdout, stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def remote_tee(self, command: str, log_path: Path) -> int:
        self.remote_commands.append(command)
        return self.upload_status


class PullRequestTests(unittest.TestCase):
    def test_shell_wrapper_executes_python_module_from_its_own_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            wrapper = root / "cvm-pull.sh"
            module = root / "cvm_pull.py"
            wrapper.write_text(
                (REPO_ROOT / "scripts/pilot/cvm-pull.sh").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            module.write_text(
                "import sys\nprint('|'.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [wrapper, "--exp", "group/exp"],
                cwd="/tmp",
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "--exp|group/exp")

    def test_parse_request_validates_scope_and_policy(self) -> None:
        request = cvm_pull.parse_request(
            [
                "--exp",
                "20260805-170000-audit/20260805-170001-airplane",
                "--include-byproducts",
                "--retain-cvm-source",
            ]
        )
        self.assertEqual(
            request.exp,
            "20260805-170000-audit/20260805-170001-airplane",
        )
        self.assertIs(
            request.postmortem_policy,
            cvm_pull.PostmortemPolicy.INCLUDE_RETAIN,
        )

        with self.assertRaisesRegex(
            cvm_pull.PullError,
            "--retain-cvm-source requires --include-byproducts",
        ):
            cvm_pull.parse_request(["--exp", "group/exp", "--retain-cvm-source"])

        with self.assertRaisesRegex(cvm_pull.PullError, "Unsafe --exp"):
            cvm_pull.parse_request(["--exp", "../escape"])


class PullPlanTests(unittest.TestCase):
    def workflow(self, runner: FakeRunner):
        request = cvm_pull.PullRequest(
            None,
            None,
            cvm_pull.PostmortemPolicy.DEFAULT,
        )
        workflow = cvm_pull.CvmPull(request, runner)
        workflow.mount_path = Path("/nonexistent-test-mount")
        return workflow

    def test_qualify_fails_all_candidates_before_publish(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        payloads = {
            "group/complete": {
                "path_safe": True,
                "complete": True,
                "final_status": 0,
                "has_postmortem": False,
            },
            "group/incomplete": {
                "path_safe": True,
                "complete": False,
                "final_status": None,
                "has_postmortem": False,
            },
        }

        def remote(command: str, *, check: bool = True):
            for exp, payload in payloads.items():
                if exp in command:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(payload),
                        stderr="",
                    )
            raise AssertionError(command)

        runner.remote = remote
        with self.assertRaisesRegex(cvm_pull.PullError, "group/incomplete") as error:
            workflow.qualify(
                ("group/complete", "group/incomplete"),
                ("group/complete", "group/incomplete"),
            )
        self.assertEqual(error.exception.status, 9)

    def test_non_object_manifest_is_incomplete_not_transport_failure(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("isinstance(manifest, dict)", source)
        self.assertIn("value = None", source)

    def test_inspection_rejects_symlinked_output_ancestor_before_manifest(self) -> None:
        runner = FakeRunner()
        runner.respond(
            "python3 -c",
            stdout=json.dumps(
                {
                    "path_safe": False,
                    "complete": False,
                    "final_status": None,
                    "has_postmortem": False,
                }
            ),
        )
        workflow = self.workflow(runner)
        with self.assertRaisesRegex(
            cvm_pull.PullError,
            "Unsafe CVM exp path",
        ) as error:
            workflow._inspect_exp("group/exp")
        self.assertEqual(error.exception.status, 7)
        command = runner.remote_commands[-1]
        self.assertIn("path.lstat().st_mode", command)
        self.assertIn("stat.S_ISDIR", command)

    def test_qualify_preserves_every_nonzero_integer_and_postmortem(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        payloads = {
            "group/success": {
                "path_safe": True,
                "complete": True,
                "final_status": 0,
                "has_postmortem": False,
            },
            "group/negative": {
                "path_safe": True,
                "complete": True,
                "final_status": -9,
                "has_postmortem": False,
            },
            "group/upper": {
                "path_safe": True,
                "complete": True,
                "final_status": 0,
                "has_postmortem": True,
            },
        }

        def remote(command: str, *, check: bool = True):
            for exp, payload in payloads.items():
                if exp in command:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(payload),
                        stderr="",
                    )
            raise AssertionError(command)

        runner.remote = remote
        plan = workflow.qualify(tuple(payloads), tuple(payloads))
        self.assertEqual(plan.publish, ("group/success",))
        self.assertEqual(
            tuple(item.exp for item in plan.preserve),
            ("group/negative", "group/upper"),
        )

    def test_discovery_uses_scoped_single_exp_and_lists_s3_prefixes(self) -> None:
        runner = FakeRunner()
        runner.respond("test -d", status=0)
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(
                "group/exp",
                None,
                cvm_pull.PostmortemPolicy.DEFAULT,
            ),
            runner,
        )
        workflow.mount_path = Path("/tmp/cvm-pull-test-mount")
        with mock.patch.object(Path, "mkdir"):
            cvm_exps, s3_exps = workflow.discover_candidates()
        self.assertEqual(cvm_exps, ("group/exp",))
        self.assertEqual(s3_exps, frozenset())
        self.assertIn("test -d ~/text-to-cad/outputs/group/exp", runner.remote_commands)
        self.assertTrue(
            any(command[:2] == ("rclone", "lsf") for command in runner.local)
        )

    def test_group_discovery_excludes_internal_terminal_directory_at_find(self) -> None:
        runner = FakeRunner()
        runner.respond(
            "find ~/text-to-cad/outputs/group/",
            stdout="group/.internal-terminal-validation\ngroup/exp\n",
        )
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(None, "group", cvm_pull.PostmortemPolicy.DEFAULT),
            runner,
        )
        cvm_exps, _s3_exps = workflow.discover_candidates()
        self.assertEqual(("group/exp",), cvm_exps)
        find_command = next(
            command
            for command in runner.remote_commands
            if command.startswith("find ~/text-to-cad/outputs/group/")
        )
        self.assertIn("! -name '.internal-terminal-validation'", find_command)

    def test_unscoped_discovery_excludes_internal_terminal_directory_at_find(self) -> None:
        runner = FakeRunner()
        runner.respond(
            "find ~/text-to-cad/outputs/",
            stdout="group/.internal-terminal-validation\ngroup/exp\nother/exp2\n",
        )
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(None, None, cvm_pull.PostmortemPolicy.DEFAULT),
            runner,
        )
        cvm_exps, _s3_exps = workflow.discover_candidates()
        self.assertEqual(("group/exp", "other/exp2"), cvm_exps)
        find_command = next(
            command
            for command in runner.remote_commands
            if command.startswith("find ~/text-to-cad/outputs/")
        )
        self.assertIn("! -name '.internal-terminal-validation'", find_command)

    def test_publish_orders_upload_verify_cleanup_and_fails_closed(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/exp",),
            candidates=("group/exp",),
            s3_exps=frozenset(),
            publish=("group/exp",),
            preserve=(),
        )
        events: list[str] = []
        with (
            mock.patch.object(
                workflow,
                "_upload_exp",
                side_effect=lambda _exp: events.append("upload"),
            ),
            mock.patch.object(
                workflow,
                "_verify_exp",
                side_effect=lambda _exp: events.append("verify") or 3,
            ),
            mock.patch.object(
                workflow,
                "_cleanup_exp",
                side_effect=lambda _exp: events.append("cleanup"),
            ),
        ):
            result = workflow.publish(plan)
        self.assertEqual(events, ["upload", "verify", "cleanup"])
        self.assertEqual(result.uploaded, ("group/exp",))

        events.clear()
        with (
            mock.patch.object(
                workflow,
                "_upload_exp",
                side_effect=lambda _exp: events.append("upload"),
            ),
            mock.patch.object(
                workflow,
                "_verify_exp",
                side_effect=cvm_pull.PullError("bad verify", 5),
            ),
            mock.patch.object(
                workflow,
                "_cleanup_exp",
                side_effect=lambda _exp: events.append("cleanup"),
            ),
        ):
            with self.assertRaises(cvm_pull.PullError):
                workflow.publish(plan)
        self.assertEqual(events, ["upload"])

    def test_publish_can_verify_postmortem_without_cleaning_cvm_source(self) -> None:
        runner = FakeRunner()
        request = cvm_pull.PullRequest(
            None,
            None,
            cvm_pull.PostmortemPolicy.INCLUDE_RETAIN,
        )
        workflow = cvm_pull.CvmPull(request, runner)
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/failed",),
            candidates=("group/failed",),
            s3_exps=frozenset(),
            publish=("group/failed",),
            preserve=(),
        )
        events: list[str] = []
        with (
            mock.patch.object(
                workflow,
                "_upload_exp",
                side_effect=lambda _exp: events.append("upload"),
            ),
            mock.patch.object(
                workflow,
                "_verify_exp",
                side_effect=lambda _exp: events.append("verify") or 7,
            ),
            mock.patch.object(
                workflow,
                "_cleanup_exp",
                side_effect=lambda _exp: events.append("cleanup"),
            ),
        ):
            result = workflow.publish(plan)
        self.assertEqual(events, ["upload", "verify"])
        self.assertEqual(result.uploaded, ("group/failed",))
        self.assertEqual(result.retained_source, ("group/failed",))

    def test_existing_verified_postmortem_is_not_reported_as_uploaded(self) -> None:
        runner = FakeRunner()
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(
                None,
                None,
                cvm_pull.PostmortemPolicy.INCLUDE_RETAIN,
            ),
            runner,
        )
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/failed",),
            candidates=("group/failed",),
            s3_exps=frozenset({"group/failed"}),
            publish=("group/failed",),
            preserve=(),
        )
        with (
            mock.patch.object(
                workflow,
                "_existing_s3_is_complete",
                return_value=(True, 7, 7),
            ),
            mock.patch.object(workflow, "_upload_exp") as upload,
            mock.patch.object(workflow, "_cleanup_exp") as cleanup,
        ):
            result = workflow.publish(plan)
        upload.assert_not_called()
        cleanup.assert_not_called()
        self.assertEqual(result.uploaded, ())
        self.assertEqual(result.verified_existing, ("group/failed",))
        self.assertEqual(result.retained_source, ("group/failed",))

        with mock.patch.object(workflow, "_log") as log:
            workflow.report(result)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(
            any(
                message.startswith("Existing S3 prefix verified")
                for message in messages
            )
        )
        self.assertIn("Retained CVM source:", messages)

    def test_publish_resumes_cleanup_for_verified_existing_s3_prefix(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/exp",),
            candidates=("group/exp",),
            s3_exps=frozenset({"group/exp"}),
            publish=("group/exp",),
            preserve=(),
        )
        events: list[str] = []
        with (
            mock.patch.object(
                workflow,
                "_existing_s3_is_complete",
                return_value=(True, 3, 3),
            ),
            mock.patch.object(
                workflow,
                "_upload_exp",
                side_effect=lambda _exp: events.append("upload"),
            ),
            mock.patch.object(
                workflow,
                "_cleanup_exp",
                side_effect=lambda _exp: events.append("cleanup"),
            ),
        ):
            workflow.publish(plan)
        self.assertEqual(events, ["cleanup"])

    def test_publish_repairs_partial_s3_prefix_before_cleanup(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/exp",),
            candidates=("group/exp",),
            s3_exps=frozenset({"group/exp"}),
            publish=("group/exp",),
            preserve=(),
        )
        events: list[str] = []
        with (
            mock.patch.object(
                workflow,
                "_existing_s3_is_complete",
                return_value=(False, 3, 1),
            ),
            mock.patch.object(
                workflow,
                "_upload_exp",
                side_effect=lambda _exp: events.append("upload"),
            ),
            mock.patch.object(
                workflow,
                "_verify_exp",
                side_effect=lambda _exp: events.append("verify") or 3,
            ),
            mock.patch.object(
                workflow,
                "_cleanup_exp",
                side_effect=lambda _exp: events.append("cleanup"),
            ),
        ):
            workflow.publish(plan)
        self.assertEqual(events, ["upload", "verify", "cleanup"])

    def test_publish_preserves_source_when_s3_has_extra_objects(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        plan = cvm_pull.PullPlan(
            cvm_exps=("group/exp",),
            candidates=("group/exp",),
            s3_exps=frozenset({"group/exp"}),
            publish=("group/exp",),
            preserve=(),
        )
        with (
            mock.patch.object(
                workflow,
                "_existing_s3_is_complete",
                return_value=(False, 3, 4),
            ),
            mock.patch.object(workflow, "_upload_exp") as upload,
            mock.patch.object(workflow, "_cleanup_exp") as cleanup,
        ):
            with self.assertRaises(cvm_pull.PullError) as error:
                workflow.publish(plan)
        self.assertEqual(error.exception.status, 5)
        upload.assert_not_called()
        cleanup.assert_not_called()

    def test_count_local_files_uses_relative_fnmatch_contract(self) -> None:
        runner = FakeRunner()
        runner.respond(
            "path.relative_to(root).as_posix()",
            stdout=json.dumps([f"file-{index}" for index in range(7)]),
        )
        workflow = self.workflow(runner)
        workflow.excludes = ("stderr.log", ".git/*")
        self.assertEqual(workflow._count_local_files("group/exp"), 7)
        command = runner.remote_commands[-1]
        self.assertIn("path.relative_to(root).as_posix()", command)
        self.assertIn("fnmatch.fnmatch(relative, pattern)", command)
        self.assertIn("stat.S_ISREG(path.lstat().st_mode)", command)

    def test_disposable_browser_runtimes_are_always_excluded(self) -> None:
        runner = FakeRunner()
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(
                None,
                None,
                cvm_pull.PostmortemPolicy.INCLUDE_RETAIN,
            ),
            runner,
        )

        self.assertEqual(
            cvm_pull.DISPOSABLE_RUNTIME_EXCLUDES,
            workflow.excludes,
        )
        workflow._upload_exp("group/exp")
        command = runner.remote_commands[-1]
        for pattern in cvm_pull.DISPOSABLE_RUNTIME_EXCLUDES:
            self.assertIn(f"--exclude '{pattern}'", command)
        self.assertNotIn(".codex-home", command)

    def test_full_postmortem_excludes_browser_and_git_lfs_caches(
        self,
    ) -> None:
        runner = FakeRunner()
        workflow = cvm_pull.CvmPull(
            cvm_pull.PullRequest(
                None,
                None,
                cvm_pull.PostmortemPolicy.INCLUDE_RETAIN,
            ),
            runner,
        )

        self.assertIn("work/playwright-browsers/*", workflow.excludes)
        self.assertIn("*/run/playwright/*", workflow.excludes)
        self.assertIn("*/run/playwright-browsers/*", workflow.excludes)
        self.assertIn("*/work/playwright/*", workflow.excludes)
        self.assertIn("*/work/playwright-browsers/*", workflow.excludes)
        self.assertIn(".git/lfs/*", workflow.excludes)
        self.assertIn("*/.git/lfs/*", workflow.excludes)
        self.assertNotIn(".git/*", workflow.excludes)
        self.assertNotIn("*/.git/*", workflow.excludes)

    def test_default_excludes_include_disposable_browser_runtimes(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)

        for pattern in cvm_pull.DISPOSABLE_RUNTIME_EXCLUDES:
            self.assertIn(pattern, workflow.excludes)
        self.assertEqual(
            len(workflow.excludes),
            len(set(workflow.excludes)),
        )

    def test_default_excludes_preserve_workspace_stderr_inventory(self) -> None:
        workflow = self.workflow(FakeRunner())

        def excluded(relative: str) -> bool:
            return any(fnmatch.fnmatch(relative, pattern) for pattern in workflow.excludes)

        self.assertFalse(excluded("run/stderr.log"))
        self.assertFalse(excluded("attempts/000001/commands/000001/stderr.log"))
        self.assertFalse(excluded("cycles/000001/commands/000001/stderr.log"))

    def test_upload_never_follows_cvm_symlinks(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        workflow._upload_exp("group/exp")
        self.assertIn("--no-follow-symlinks", runner.remote_commands[-1])

    def test_terminal_locator_sidecar_is_forced_through_transfer_filters(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        workflow.excludes = ("run/*",)
        self.assertEqual(
            cvm_pull.TERMINAL_LOCATOR_RELATIVE,
            "run/terminal-validation-locator.json",
        )
        workflow._upload_exp("group/exp")
        command = runner.remote_commands[-1]
        self.assertIn("--exclude 'run/*'", command)
        self.assertIn("--include run/terminal-validation-locator.json", command)
        runner.respond(
            "path.relative_to(root).as_posix()",
            stdout=json.dumps(["run/terminal-validation-locator.json"]),
        )
        self.assertEqual(
            ("run/terminal-validation-locator.json",),
            workflow._list_local_files("group/exp"),
        )

    def test_s3_listing_filters_relative_keys_with_the_same_excludes(self) -> None:
        runner = FakeRunner()
        runner.respond(
            "s3api",
            stdout=json.dumps([f"file-{index}" for index in range(7)]),
        )
        workflow = self.workflow(runner)
        self.assertEqual(workflow._count_s3_files("group/exp"), 7)
        command = runner.remote_commands[-1]
        self.assertIn("list-objects-v2", command)
        self.assertIn("fnmatch.fnmatch(relative, pattern)", command)
        self.assertIn("ericzyma/text-to-cad/outputs/group/exp/", command)

    def test_existing_s3_requires_exact_filtered_key_set(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        with (
            mock.patch.object(
                workflow,
                "_list_local_files",
                return_value=("artifact_manifest.json", "step_index.json", "workspace.json"),
            ),
            mock.patch.object(
                workflow,
                "_list_s3_files",
                return_value=("artifact_manifest.json", "wrong-offsetting-object.json", "workspace.json"),
            ),
        ):
            complete, local_count, s3_count = workflow._existing_s3_is_complete(
                "group/exp"
            )

        self.assertFalse(complete)
        self.assertEqual((3, 3), (local_count, s3_count))

    def test_verify_reports_missing_and_extra_filtered_keys(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        with (
            mock.patch.object(
                workflow,
                "_list_local_files",
                return_value=("artifact_manifest.json", "step_index.json"),
            ),
            mock.patch.object(
                workflow,
                "_list_s3_files",
                return_value=("artifact_manifest.json", "run/playwright-version.json"),
            ),
        ):
            with self.assertRaises(cvm_pull.PullError) as error:
                workflow._verify_exp("group/exp")

        self.assertEqual(5, error.exception.status)
        self.assertIn("step_index.json", str(error.exception))
        self.assertIn("run/playwright-version.json", str(error.exception))

    def test_cleanup_revalidates_exact_two_component_target(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        with self.assertRaisesRegex(cvm_pull.PullError, "unsafe cleanup"):
            workflow._cleanup_exp("../escape")
        self.assertEqual(runner.remote_commands, [])

        workflow._cleanup_exp("group/exp")
        self.assertEqual(
            runner.remote_commands[-1],
            "rm -rf -- ~/text-to-cad/outputs/group/exp "
            "~/text-to-cad/outputs/group/.internal-terminal-validation/exp",
        )

    def test_cleanup_removes_only_matching_external_handoff_for_reused_exp_name(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        workflow._cleanup_exp("group/reused")
        self.assertEqual(
            runner.remote_commands[-1],
            "rm -rf -- ~/text-to-cad/outputs/group/reused "
            "~/text-to-cad/outputs/group/.internal-terminal-validation/reused",
        )
        with self.assertRaisesRegex(cvm_pull.PullError, "unsafe cleanup"):
            workflow._cleanup_exp("group/reused/extra")

    def test_expose_refreshes_parent_group_exp_before_visibility(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        result = cvm_pull.PublishResult(("group/exp",), ())
        refreshes: list[str] = []
        with (
            mock.patch.object(
                workflow,
                "_refresh_dir",
                side_effect=lambda directory: refreshes.append(directory),
            ),
            mock.patch.object(Path, "is_dir", return_value=True),
        ):
            workflow.expose(result)
        self.assertEqual(
            refreshes,
            [
                "ericzyma/text-to-cad/outputs",
                "ericzyma/text-to-cad/outputs/group",
                "ericzyma/text-to-cad/outputs/group/exp",
            ],
        )

    def test_mount_visibility_failure_reports_status_six(self) -> None:
        runner = FakeRunner()
        workflow = self.workflow(runner)
        result = cvm_pull.PublishResult(("group/exp",), ())
        with (
            mock.patch.object(workflow, "_refresh_dir"),
            mock.patch.object(Path, "is_dir", return_value=False),
            mock.patch.object(cvm_pull.time, "sleep"),
        ):
            with self.assertRaises(cvm_pull.PullError) as error:
                workflow.expose(result)
        self.assertEqual(error.exception.status, 6)


if __name__ == "__main__":
    unittest.main()
