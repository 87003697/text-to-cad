from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT


MODULE_PATH = REPO_ROOT / "scripts" / "pilot" / "cvm_push.py"
SPEC = importlib.util.spec_from_file_location("cvm_push", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cvm_push = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cvm_push
SPEC.loader.exec_module(cvm_push)


class FakeRunner:
    def __init__(self) -> None:
        self.local: list[tuple[tuple[str, ...], Path]] = []
        self.remote_commands: list[str] = []
        self.streams: list[tuple[tuple[str, ...], Path]] = []
        self.responses: list[tuple[str, int, str]] = []
        self.stream_status = 0
        self.stream_output = ""

    def respond(self, marker: str, stdout: str = "", status: int = 0) -> None:
        self.responses.append((marker, status, stdout))

    def run(self, argv, *, cwd, check=True, env=None):
        self.local.append((tuple(argv), Path(cwd)))
        return mock.Mock(returncode=0, stdout="", stderr="")

    def remote(self, command: str, *, cwd, check=True):
        self.remote_commands.append(command)
        for marker, status, stdout in self.responses:
            if marker in command:
                return mock.Mock(returncode=status, stdout=stdout, stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def stream(self, argv, *, cwd, log_path, env=None, echo):
        self.streams.append((tuple(argv), Path(cwd)))
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write(self.stream_output)
        if echo:
            print(self.stream_output, end="")
        return self.stream_status


def write_package(root: Path, name: str, version: str) -> None:
    path = root.joinpath(*name.split("/"), "package.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")


def make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def create_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "scripts/pilot").mkdir(parents=True)
    (repo / "viewer").mkdir()
    (repo / "AGENTS.md").write_text("test\n", encoding="utf-8")
    (repo / ".cvmignore").write_text(
        ".git/\n.git\nnode_modules/\n/viewer/\n.cvm-jobs/\n",
        encoding="utf-8",
    )
    (repo / "viewer/package-lock.json").write_text(
        (REPO_ROOT / "viewer/package-lock.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return repo


def create_viewer_dependencies(root: Path) -> Path:
    lock = json.loads(
        (REPO_ROOT / "viewer/package-lock.json").read_text(encoding="utf-8")
    )
    modules = root / "viewer/node_modules"
    for executable in cvm_push.VIEWER_REQUIRED_EXECUTABLES:
        make_executable(modules / executable)
    for package in cvm_push.VIEWER_REQUIRED_PACKAGES:
        version = lock["packages"][f"node_modules/{package}"]["version"]
        write_package(modules, package, version)
    return modules


def create_cad_dependencies(root: Path) -> Path:
    build = root / "tmp/cad-snapshot-build"
    for executable in cvm_push.CAD_REQUIRED_EXECUTABLES:
        make_executable(build / executable)
    for package, version in cvm_push.CAD_REQUIRED_PACKAGE_VERSIONS.items():
        write_package(build / "node_modules", package, version)
    return build


def create_runtime(stage: Path) -> cvm_push.RuntimeAttestation:
    for directory in cvm_push.PRODUCTION_RUNTIME.physical_directories:
        (stage / directory).mkdir(parents=True, exist_ok=True)
    for relative in cvm_push.PRODUCTION_RUNTIME.required_files:
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}\n", encoding="utf-8")
    workflow = cvm_push.CvmPush(FakeRunner(), repo_root=stage, environ={})
    workflow.validate_stage(stage)
    return workflow.attest_stage(stage)


class PushWrapperTests(unittest.TestCase):
    def test_shell_wrapper_executes_python_module_from_its_own_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            wrapper = root / "cvm-push.sh"
            module = root / "cvm_push.py"
            shutil.copy2(REPO_ROOT / "scripts/pilot/cvm-push.sh", wrapper)
            module.write_text(
                "import sys\nprint('|'.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [wrapper, "one", "two"],
                cwd="/tmp",
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "one|two")


class AgentModeTests(unittest.TestCase):
    def test_cli_accepts_agent_and_rejects_unknown_arguments(self) -> None:
        self.assertTrue(cvm_push.parse_args(["--agent"]).agent)
        with self.assertRaises(SystemExit) as error:
            cvm_push.parse_args(["--unknown"])
        self.assertEqual(error.exception.code, 2)

    def test_agent_transfer_is_quiet_while_manual_transfer_keeps_progress(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            stage = root / "stage"
            stage.mkdir()
            runner = FakeRunner()
            runner.stream_output = "rsync progress\n"

            agent_stdout = io.StringIO()
            with redirect_stdout(agent_stdout):
                cvm_push.CvmPush(
                    runner,
                    repo_root=repo,
                    environ={"TMPDIR": str(root)},
                    agent=True,
                ).transfer_stage(stage)

            manual_stdout = io.StringIO()
            with redirect_stdout(manual_stdout):
                cvm_push.CvmPush(
                    runner,
                    repo_root=repo,
                    environ={"TMPDIR": str(root)},
                ).transfer_stage(stage)

        self.assertEqual(agent_stdout.getvalue(), "")
        self.assertEqual(manual_stdout.getvalue(), "rsync progress\n")

    def test_agent_success_emits_phases_and_matching_receipt_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=repo,
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            source = cvm_push.SourceProvenance("develop", "deadbeef", "dirty")
            attestation = cvm_push.RuntimeAttestation({})
            workflow.preflight_local = mock.Mock()
            workflow.preflight_remote = mock.Mock(
                return_value=cvm_push.RemotePreflight(free_gb=20)
            )
            workflow.inspect_source = mock.Mock(return_value=source)
            workflow.resolve_build_inputs = mock.Mock(
                return_value=cvm_push.BuildInputs(root / "viewer", root / "cad")
            )
            workflow.copy_source_to_stage = mock.Mock()
            workflow.copy_build_inputs = mock.Mock()
            workflow.materialize_skill_symlinks = mock.Mock()
            workflow.bundle_stage = mock.Mock()
            workflow.validate_stage = mock.Mock()
            workflow.attest_stage = mock.Mock(return_value=attestation)
            workflow.transfer_stage = mock.Mock()
            workflow.verify_remote = mock.Mock()
            workflow.remote_git_base = mock.Mock(return_value="remote-head")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = cvm_push.execute(workflow)

            records = [json.loads(line) for line in stdout.getvalue().splitlines()]
            receipt = records[-1]
            persisted = json.loads(
                workflow.receipt_path.read_text(encoding="utf-8")
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            [record["phase"] for record in records[:-1]],
            ["preflight", "stage", "transfer", "verify"],
        )
        self.assertTrue(
            all(record["schema"] == "cvm-push.event/1" for record in records[:-1])
        )
        self.assertEqual(receipt, persisted)
        self.assertEqual(receipt["schema"], "cvm-push.receipt/1")
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["phase"], "complete")
        self.assertEqual(
            receipt["source"],
            {"branch": "develop", "head": "deadbeef", "state": "dirty"},
        )
        self.assertEqual(
            receipt["transfer"],
            {"sent_bytes": None, "received_bytes": None, "bytes_per_second": None},
        )
        self.assertEqual(receipt["remote_git_base"], "remote-head")

    def test_agent_failure_preserves_exit_code_and_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            workflow.preflight_local = mock.Mock(
                side_effect=cvm_push.PushError("local preflight failed", 4)
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cvm_push.execute(workflow)

            records = [json.loads(line) for line in stdout.getvalue().splitlines()]
            receipt = records[-1]
            persisted = json.loads(
                workflow.receipt_path.read_text(encoding="utf-8")
            )
            log = workflow.log_path.read_text(encoding="utf-8")

        self.assertEqual(status, 4)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(records[0]["phase"], "preflight")
        self.assertEqual(receipt, persisted)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 4)
        self.assertEqual(receipt["phase"], "preflight")
        self.assertEqual(receipt["error"], "local preflight failed")
        self.assertIsNone(receipt["source"])
        self.assertIn("local preflight failed", log)

    def test_agent_unexpected_exception_produces_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            workflow.preflight_local = mock.Mock(side_effect=RuntimeError("boom"))

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = cvm_push.execute(workflow)

            receipt = json.loads(stdout.getvalue().splitlines()[-1])
            log = workflow.log_path.read_text(encoding="utf-8")

        self.assertEqual(status, 1)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["error"], "boom")
        self.assertIn("RuntimeError: boom", log)

    def test_agent_keyboard_interrupt_produces_exit_130_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            workflow.preflight_local = mock.Mock(side_effect=KeyboardInterrupt())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = cvm_push.execute(workflow)

            receipt = json.loads(stdout.getvalue().splitlines()[-1])

        self.assertEqual(status, 130)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 130)
        self.assertEqual(receipt["error"], "interrupted")

    def test_agent_receipt_write_failure_falls_back_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
                agent=True,
            )

            def succeed_without_remote_work() -> None:
                workflow.phase = "complete"

            workflow.run = mock.Mock(side_effect=succeed_without_remote_work)
            workflow.write_receipt = mock.Mock(side_effect=OSError("disk full"))

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cvm_push.execute(workflow)

            receipt = json.loads(stdout.getvalue().splitlines()[-1])

        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["phase"], "complete")
        self.assertIn("disk full", receipt["receipt_write_error"])

    def test_agent_log_failure_still_emits_fallback_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            blocked = root / "blocked"
            blocked.write_text("not a directory\n", encoding="utf-8")
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(blocked)},
                agent=True,
            )
            workflow.preflight_local = mock.Mock(
                side_effect=cvm_push.PushError("local preflight failed", 4)
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cvm_push.execute(workflow)

            receipt = json.loads(stdout.getvalue().splitlines()[-1])

        self.assertEqual(status, 4)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 4)
        self.assertEqual(receipt["error"], "local preflight failed")
        self.assertIn("FileExistsError", receipt["log_write_error"])
        self.assertIn("FileExistsError", receipt["receipt_write_error"])

    def test_staging_diagnostic_log_failure_preserves_original_exit_code(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            workflow.preflight_local = mock.Mock()
            workflow.preflight_remote = mock.Mock(
                return_value=cvm_push.RemotePreflight(free_gb=20)
            )
            workflow.inspect_source = mock.Mock(
                return_value=cvm_push.SourceProvenance(
                    "develop", "deadbeef", "dirty"
                )
            )
            workflow.resolve_build_inputs = mock.Mock(
                side_effect=cvm_push.PushError("missing inputs", 4)
            )
            original_log = workflow._log

            def fail_only_for_staging_diagnostic(message, *, stderr=False):
                if message == "CVM production staging failed; no files transferred.":
                    raise OSError("diagnostic log unavailable")
                return original_log(message, stderr=stderr)

            workflow._log = mock.Mock(side_effect=fail_only_for_staging_diagnostic)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = cvm_push.execute(workflow)

            receipt = json.loads(stdout.getvalue().splitlines()[-1])

        self.assertEqual(status, 4)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 4)
        self.assertEqual(receipt["phase"], "stage")
        self.assertEqual(receipt["error"], "missing inputs")


class BuildInputTests(unittest.TestCase):
    def test_incomplete_worktree_inputs_fall_back_to_complete_primary(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            primary = root / "primary"
            viewer = create_viewer_dependencies(primary)
            cad = create_cad_dependencies(primary)
            (repo / "viewer/node_modules/react").mkdir(parents=True)
            (repo / "tmp/cad-snapshot-build/node_modules").mkdir(parents=True)

            runner = FakeRunner()

            def run(argv, *, cwd, check=True, env=None):
                if tuple(argv) == (
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ):
                    return mock.Mock(
                        returncode=0,
                        stdout=f"{primary / '.git'}\n",
                        stderr="",
                    )
                raise AssertionError(argv)

            runner.run = run
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={},
            )
            inputs = workflow.resolve_build_inputs()

            self.assertEqual(inputs.viewer_node_modules, viewer.resolve())
            self.assertEqual(inputs.cad_build_dependencies, cad.resolve())

    def test_incomplete_explicit_source_fails_instead_of_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            explicit = root / "incomplete"
            explicit.mkdir()
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=repo,
                environ={
                    "CVM_PUSH_VIEWER_NODE_MODULES_SOURCE": str(explicit),
                },
            )

            with self.assertRaisesRegex(
                cvm_push.PushError,
                "Incomplete explicit Viewer dependencies",
            ) as error:
                workflow._resolve_source(
                    label="Viewer dependencies",
                    explicit_name="CVM_PUSH_VIEWER_NODE_MODULES_SOURCE",
                    candidates=[],
                    validate=lambda path: cvm_push.viewer_dependency_errors(
                        path,
                        repo,
                    ),
                )
            self.assertEqual(error.exception.status, 4)


class StageTests(unittest.TestCase):
    def test_materialize_skill_symlinks_replaces_internal_links_before_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            stage = Path(root_text)
            package = stage / "packages/meshshot"
            package.mkdir(parents=True)
            (package / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            runtime = stage / "skills/mesh-compare/scripts/packages/meshshot"
            runtime.parent.mkdir(parents=True)
            os.symlink("../../../../packages/meshshot", runtime)
            workflow = cvm_push.CvmPush(FakeRunner(), repo_root=stage, environ={})

            workflow.materialize_skill_symlinks(stage)

            self.assertTrue(runtime.is_dir())
            self.assertFalse(runtime.is_symlink())
            self.assertEqual(
                (runtime / "pyproject.toml").read_text(encoding="utf-8"),
                "[project]\n",
            )

    def test_source_copy_excludes_private_state_and_keeps_dirty_source(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            (repo / "packages/source.txt").parent.mkdir(parents=True)
            (repo / "packages/source.txt").write_text("dirty\n", encoding="utf-8")
            (repo / ".agents/secret.txt").parent.mkdir()
            (repo / ".agents/secret.txt").write_text("secret\n", encoding="utf-8")
            (repo / ".codex/config.toml").parent.mkdir()
            (repo / ".codex/config.toml").write_text("secret\n", encoding="utf-8")
            (repo / "viewer/dist/index.html").parent.mkdir()
            (repo / "viewer/dist/index.html").write_text("generated\n", encoding="utf-8")
            (repo / "scripts/cache/__pycache__/x.pyc").parent.mkdir(parents=True)
            (repo / "scripts/cache/__pycache__/x.pyc").write_bytes(b"x")

            workflow = cvm_push.CvmPush(
                cvm_push.CommandRunner(),
                repo_root=repo,
                environ=os.environ,
            )
            stage = root / "stage"
            stage.mkdir()
            workflow.copy_source_to_stage(stage)

            self.assertEqual(
                (stage / "packages/source.txt").read_text(encoding="utf-8"),
                "dirty\n",
            )
            self.assertFalse((stage / ".agents").exists())
            self.assertFalse((stage / ".codex").exists())
            self.assertFalse((stage / "viewer/dist").exists())
            self.assertFalse((stage / "scripts/cache/__pycache__").exists())

    def test_build_input_copy_does_not_follow_checkout_package_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            viewer = create_viewer_dependencies(root / "inputs")
            cad = create_cad_dependencies(root / "inputs")
            outside = root / "outside"
            (outside / "cadjs").mkdir(parents=True)
            (outside / "cadjs/secret.txt").write_text("source\n", encoding="utf-8")
            os.symlink(outside / "cadjs", viewer / "cadjs")
            stage = root / "stage"
            stage.mkdir()
            workflow = cvm_push.CvmPush(
                cvm_push.CommandRunner(),
                repo_root=repo,
                environ=os.environ,
            )

            workflow.copy_build_inputs(
                stage,
                cvm_push.BuildInputs(viewer, cad),
            )

            cadjs_link = stage / "viewer/node_modules/cadjs"
            self.assertTrue(cadjs_link.is_symlink())
            self.assertEqual(os.readlink(cadjs_link), "../packages/cadjs")
            self.assertTrue(
                (
                    stage
                    / "tmp/cad-snapshot-build/node_modules/three/package.json"
                ).is_file()
            )

    def test_stage_cleanup_runs_on_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )
            with workflow.deployment_stage() as stage:
                self.assertTrue(stage.is_dir())
            self.assertFalse(stage.exists())

            with self.assertRaisesRegex(RuntimeError, "boom"):
                with workflow.deployment_stage() as failed_stage:
                    raise RuntimeError("boom")
            self.assertFalse(failed_stage.exists())

    def test_validate_stage_rejects_symlink_and_missing_contract_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            stage = Path(root_text)
            attestation = create_runtime(stage)
            self.assertTrue(attestation.hashes)

            required = stage / cvm_push.PRODUCTION_RUNTIME.required_files[-1]
            required.unlink()
            workflow = cvm_push.CvmPush(FakeRunner(), repo_root=stage, environ={})
            with self.assertRaisesRegex(cvm_push.PushError, "stage is missing"):
                workflow.validate_stage(stage)

            required.write_text("{}\n", encoding="utf-8")
            runtime = stage / cvm_push.PRODUCTION_RUNTIME.physical_directories[0]
            shutil.rmtree(runtime)
            os.symlink(stage / "elsewhere", runtime)
            with self.assertRaisesRegex(
                cvm_push.PushError,
                "skill symlink|physical directory",
            ):
                workflow.validate_stage(stage)


class TransferAndVerifyTests(unittest.TestCase):
    def test_transfer_uses_one_rsync_with_repository_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            runner = FakeRunner()
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )
            stage = root / "stage"
            stage.mkdir()
            workflow.transfer_stage(stage)

            self.assertEqual(len(runner.streams), 1)
            argv = runner.streams[0][0]
            exclude = f"--exclude-from={repo.resolve() / '.cvmignore'}"
            self.assertIn(exclude, argv)
            self.assertEqual(argv[-2], f"{stage}/")
            self.assertEqual(argv[-1], cvm_push.REMOTE_DESTINATION)

    def test_transfer_retains_only_final_rsync_summary(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            runner = FakeRunner()
            runner.stream_output = (
                "file progress 73%\n"
                "sent 1,234 bytes  received 56 bytes  789.50 bytes/sec\n"
            )
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            stage = root / "stage"
            stage.mkdir()

            workflow.transfer_stage(stage)

        self.assertEqual(
            workflow.transfer_summary,
            cvm_push.TransferSummary(
                sent_bytes=1234,
                received_bytes=56,
                bytes_per_second=789.5,
            ),
        )

    def test_transfer_does_not_reuse_summary_from_earlier_log_output(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            runner = FakeRunner()
            runner.stream_output = "transfer completed without a summary\n"
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            workflow.log_path.write_text(
                "sent 9,999 bytes  received 88 bytes  77.0 bytes/sec\n",
                encoding="utf-8",
            )
            stage = root / "stage"
            stage.mkdir()

            workflow.transfer_stage(stage)

        self.assertEqual(workflow.transfer_summary, cvm_push.TransferSummary())

    def test_malformed_transfer_summary_does_not_change_success(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            runner = FakeRunner()
            runner.stream_output = (
                "sent ,,, bytes  received 56 bytes  789.50 bytes/sec\n"
            )
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"TMPDIR": str(root)},
                agent=True,
            )
            stage = root / "stage"
            stage.mkdir()

            workflow.transfer_stage(stage)

        self.assertEqual(workflow.transfer_summary, cvm_push.TransferSummary())

    def test_rsync_failure_is_marked_as_post_transfer_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            runner = FakeRunner()
            runner.stream_status = 23
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )
            stage = root / "stage"
            stage.mkdir()

            with self.assertRaises(cvm_push.PushError) as error:
                workflow.transfer_stage(stage)
            self.assertEqual(error.exception.status, 23)
            self.assertTrue(error.exception.transferred)

    def test_real_rsync_filter_keeps_nested_runtime_and_excludes_root_viewer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            source = root / "source"
            target = root / "target"
            repo = create_repo(root)
            root_viewer = source / "viewer/root.txt"
            nested_viewer = (
                source / "skills/cad-viewer/scripts/viewer/nested.txt"
            )
            for path in (root_viewer, nested_viewer):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
            target.mkdir()

            result = subprocess.run(
                [
                    "rsync",
                    "-a",
                    f"--exclude-from={repo / '.cvmignore'}",
                    f"{source}/",
                    f"{target}/",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((target / "viewer").exists())
            self.assertTrue(
                (target / "skills/cad-viewer/scripts/viewer/nested.txt").is_file()
            )

    def test_remote_verification_uses_full_contract_and_exact_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            stage = Path(root_text)
            attestation = create_runtime(stage)
            runner = FakeRunner()
            remote_output = "".join(
                f"{relative}\t{digest}\n"
                for relative, digest in attestation.hashes.items()
            )
            runner.respond("sha256sum", remote_output)
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=stage,
                environ={},
            )

            workflow.verify_remote(attestation)
            command = runner.remote_commands[0]
            self.assertIn("test ! -e plugins", command)
            for relative in cvm_push.PRODUCTION_RUNTIME.physical_directories:
                self.assertIn(f"test ! -L {relative}", command)
            for relative in cvm_push.PRODUCTION_RUNTIME.required_files:
                self.assertIn(f"test -f {relative}", command)

            bad = dict(attestation.hashes)
            first = next(iter(bad))
            bad[first] = "0" * 64
            with self.assertRaisesRegex(cvm_push.PushError, "hash mismatch") as error:
                workflow.verify_remote(
                    cvm_push.RuntimeAttestation(hashes=bad)
                )
            self.assertEqual(error.exception.status, 5)

    def test_legacy_plugin_cleanup_is_strictly_scoped(self) -> None:
        runner = FakeRunner()
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        workflow.remove_legacy_plugin_tree()

        self.assertEqual(len(runner.remote_commands), 1)
        command = runner.remote_commands[0]
        self.assertIn("cd ~/text-to-cad", command)
        self.assertIn("rm -rf -- plugins", command)
        self.assertIn("test ! -e plugins", command)
        self.assertNotIn("--delete", command)


class WorkflowTests(unittest.TestCase):
    def test_staging_failure_exits_before_remote_transfer(self) -> None:
        runner = FakeRunner()
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})
        workflow.preflight_local = mock.Mock()
        workflow.preflight_remote = mock.Mock(
            return_value=cvm_push.RemotePreflight(free_gb=20)
        )
        workflow.inspect_source = mock.Mock(
            return_value=cvm_push.SourceProvenance(
                branch="test",
                head="deadbeef",
                state="dirty",
            )
        )
        workflow.resolve_build_inputs = mock.Mock(
            side_effect=cvm_push.PushError("missing inputs", 4)
        )

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.run()

        self.assertEqual(error.exception.status, 4)
        self.assertEqual(runner.streams, [])
        self.assertEqual(runner.remote_commands, [])

    def test_main_workflow_orders_stage_transfer_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            workflow = cvm_push.CvmPush(
                FakeRunner(),
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )
            events: list[str] = []
            inputs = cvm_push.BuildInputs(root / "viewer", root / "cad")
            attestation = cvm_push.RuntimeAttestation(hashes={})
            workflow.preflight_local = lambda: events.append("local")
            workflow.preflight_remote = lambda: (
                events.append("remote"),
                cvm_push.RemotePreflight(free_gb=20),
            )[1]
            workflow.inspect_source = lambda: (
                events.append("source"),
                cvm_push.SourceProvenance("test", "deadbeef", "dirty"),
            )[1]
            workflow.resolve_build_inputs = lambda: (
                events.append("inputs"),
                inputs,
            )[1]
            workflow.copy_source_to_stage = lambda stage: events.append("copy-source")
            workflow.copy_build_inputs = (
                lambda stage, selected: events.append("copy-inputs")
            )
            workflow.materialize_skill_symlinks = (
                lambda stage: events.append("materialize-links")
            )
            workflow.bundle_stage = lambda stage: events.append("bundle")
            workflow.validate_stage = lambda stage: events.append("validate")
            workflow.attest_stage = lambda stage: (
                events.append("attest"),
                attestation,
            )[1]
            workflow.transfer_stage = lambda stage: events.append("transfer")
            workflow.remove_legacy_plugin_tree = lambda: events.append("cleanup-plugin")
            workflow.verify_remote = lambda expected: events.append("verify")
            workflow.remote_git_base = lambda: "remote-head"

            workflow.run()

            self.assertEqual(
                events,
                [
                    "local",
                    "remote",
                    "source",
                    "inputs",
                    "copy-source",
                    "copy-inputs",
                    "materialize-links",
                    "bundle",
                    "validate",
                    "attest",
                    "transfer",
                    "cleanup-plugin",
                    "verify",
                ],
            )


if __name__ == "__main__":
    unittest.main()
