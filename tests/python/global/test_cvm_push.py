from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import stat
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
        self.stream_envs: list[dict[str, str] | None] = []
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
        self.stream_envs.append(None if env is None else dict(env))
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


def run_fake_remote_install(
    command: str,
    *,
    python_available: bool = True,
    uv_status: int | None = 1,
    uv_output: str = "",
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as root_text:
        root = Path(root_text)
        remote = root / "text-to-cad"
        fake_bin = root / "bin"
        remote.mkdir()
        if python_available:
            make_executable(remote / ".venv/bin/python")
        if uv_status is not None:
            uv = fake_bin / "uv"
            uv.parent.mkdir()
            uv.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$FAKE_UV_OUTPUT\"\n"
                "exit \"$FAKE_UV_STATUS\"\n",
                encoding="utf-8",
            )
            uv.chmod(0o755)
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": str(root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "FAKE_UV_OUTPUT": uv_output,
                "FAKE_UV_STATUS": str(uv_status or 0),
            },
        )
        return result


def create_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "scripts/pilot").mkdir(parents=True)
    (repo / "viewer").mkdir()
    (repo / "AGENTS.md").write_text("test\n", encoding="utf-8")
    (repo / ".cvmignore").write_text(
        ".git/\n.git\nnode_modules\nnode_modules/\n/viewer/\n.cvm-jobs/\n",
        encoding="utf-8",
    )
    (repo / "viewer/package-lock.json").write_text(
        (REPO_ROOT / "viewer/package-lock.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "packages/cadjs").mkdir(parents=True)
    (repo / "packages/cadjs/package-lock.json").write_text(
        (REPO_ROOT / "packages/cadjs/package-lock.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "packages/meshshot").mkdir(parents=True)
    (repo / "packages/meshshot/package-lock.json").write_text(
        (REPO_ROOT / "packages/meshshot/package-lock.json").read_text(
            encoding="utf-8"
        ),
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


def create_cad_dependencies(root: Path, repo_root: Path | None = None) -> Path:
    build = root / "tmp/cad-snapshot-build"
    for executable in cvm_push.CAD_REQUIRED_EXECUTABLES:
        make_executable(build / executable)
    versions = cvm_push.cad_required_package_versions(repo_root or REPO_ROOT)
    for package, version in versions.items():
        write_package(build / "node_modules", package, version)
    return build


def create_meshshot_dependencies(root: Path) -> Path:
    lock = json.loads(
        (REPO_ROOT / "packages/meshshot/package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    modules = root / "packages/meshshot/node_modules"
    for executable in cvm_push.MESHSHOT_REQUIRED_EXECUTABLES:
        make_executable(modules / executable)
    for package in cvm_push.MESHSHOT_REQUIRED_PACKAGES:
        version = lock["packages"][f"node_modules/{package}"]["version"]
        write_package(modules, package, version)
    return modules


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
                return_value=cvm_push.BuildInputs(
                    root / "viewer", root / "cad", root / "meshshot"
                )
            )
            workflow.copy_source_to_stage = mock.Mock()
            workflow.copy_build_inputs = mock.Mock()
            workflow.materialize_skill_symlinks = mock.Mock()
            workflow.bundle_stage = mock.Mock()
            workflow.validate_stage = mock.Mock()
            workflow.attest_stage = mock.Mock(return_value=attestation)
            workflow.prepare_transfer_tree = mock.Mock(
                return_value=root / "transfer"
            )
            workflow.transfer_stage = mock.Mock()
            workflow.verify_remote = mock.Mock()
            authority_receipt = {
                "schema": "text-to-cad.plugin-authority/2",
                "deployment_id": "d" * 64,
                "version": "0.4.21",
                "installed_manifest_digest": "a" * 64,
            }
            workflow.install_plugin_authority = mock.Mock(
                side_effect=lambda _attestation: (
                    setattr(workflow, "plugin_authority", authority_receipt)
                    or authority_receipt
                )
            )
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
            ["preflight", "stage", "transfer", "verify", "install"],
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
        self.assertEqual(receipt["plugin_authority"], authority_receipt)

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


class RemotePreflightTests(unittest.TestCase):
    def test_preflight_checks_project_python_and_uv_before_disk(self) -> None:
        runner = FakeRunner()
        runner.respond("df --output=avail", stdout="20\n")
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        preflight = workflow.preflight_remote()

        self.assertEqual(preflight.free_gb, 20)
        self.assertEqual(len(runner.remote_commands), 3)
        python_probe, uv_probe, disk_probe = runner.remote_commands
        self.assertIn("test -x .venv/bin/python || exit 40", python_probe)
        self.assertIn(
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"',
            uv_probe,
        )
        self.assertIn("command -v uv >/dev/null 2>&1 || exit 41", uv_probe)
        self.assertIn("df --output=avail", disk_probe)

    def test_preflight_maps_missing_uv_to_stable_installer_classification(
        self,
    ) -> None:
        runner = FakeRunner()
        runner.respond("command -v uv", status=41)
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})
        workflow.resolve_build_inputs = mock.Mock()
        workflow.transfer_stage = mock.Mock()

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.run()

        self.assertEqual(error.exception.status, 5)
        self.assertFalse(error.exception.transferred)
        self.assertEqual(
            str(error.exception),
            "CVM pilot Python runtime preflight failed "
            "(classification=installer-unavailable)",
        )
        self.assertEqual(len(runner.remote_commands), 2)
        self.assertIn("test -x .venv/bin/python", runner.remote_commands[0])
        self.assertIn("command -v uv", runner.remote_commands[1])
        self.assertNotIn(
            "df --output=avail",
            "\n".join(runner.remote_commands),
        )
        workflow.resolve_build_inputs.assert_not_called()
        workflow.transfer_stage.assert_not_called()

    def test_preflight_maps_missing_python_to_stable_classification(self) -> None:
        runner = FakeRunner()
        runner.respond("test -x .venv/bin/python", status=40)
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})
        workflow.resolve_build_inputs = mock.Mock()
        workflow.transfer_stage = mock.Mock()

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.run()

        self.assertEqual(error.exception.status, 5)
        self.assertFalse(error.exception.transferred)
        self.assertEqual(
            str(error.exception),
            "CVM pilot Python runtime preflight failed "
            "(classification=python-runtime-unavailable)",
        )
        self.assertEqual(len(runner.remote_commands), 1)
        self.assertIn("test -x .venv/bin/python", runner.remote_commands[0])
        self.assertNotIn(
            "df --output=avail",
            "\n".join(runner.remote_commands),
        )
        workflow.resolve_build_inputs.assert_not_called()
        workflow.transfer_stage.assert_not_called()

    def test_preflight_preserves_disk_gate_after_runtime_probes(self) -> None:
        runner = FakeRunner()
        runner.respond("df --output=avail", stdout="2\n")
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        with self.assertRaisesRegex(cvm_push.PushError, "disk too full") as error:
            workflow.preflight_remote()

        self.assertEqual(error.exception.status, 3)
        self.assertEqual(len(runner.remote_commands), 3)
        self.assertIn("test -x .venv/bin/python", runner.remote_commands[0])
        self.assertIn("command -v uv", runner.remote_commands[1])
        self.assertIn("df --output=avail", runner.remote_commands[2])


class BuildInputTests(unittest.TestCase):
    def test_cad_dependencies_follow_cadjs_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            lock_path = repo / "packages/cadjs/package-lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"]["node_modules/three"]["version"] = "9.9.9-test"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            candidate = create_cad_dependencies(root, repo)

            self.assertEqual(
                cvm_push.cad_dependency_errors(candidate, repo),
                (),
            )
            write_package(candidate / "node_modules", "three", "0.0.0-stale")
            self.assertIn(
                "three version 0.0.0-stale does not match 9.9.9-test",
                cvm_push.cad_dependency_errors(candidate, repo),
            )

    def test_meshshot_dependencies_follow_meshshot_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            candidate = create_meshshot_dependencies(root)

            self.assertEqual(
                cvm_push.meshshot_dependency_errors(candidate, repo),
                (),
            )
            write_package(candidate, "three", "0.0.0-stale")
            self.assertIn(
                "three version 0.0.0-stale does not match 0.160.0",
                cvm_push.meshshot_dependency_errors(candidate, repo),
            )

    def test_incomplete_worktree_inputs_fall_back_to_complete_primary(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            primary = root / "primary"
            viewer = create_viewer_dependencies(primary)
            cad = create_cad_dependencies(primary)
            meshshot = create_meshshot_dependencies(primary)
            (repo / "viewer/node_modules/react").mkdir(parents=True)
            (repo / "tmp/cad-snapshot-build/node_modules").mkdir(parents=True)
            (repo / "packages/meshshot/node_modules").mkdir(parents=True)

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
            self.assertEqual(inputs.meshshot_node_modules, meshshot.resolve())

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
            (repo / "packages/source.txt").parent.mkdir(parents=True, exist_ok=True)
            (repo / "packages/source.txt").write_text("dirty\n", encoding="utf-8")
            (repo / ".agents/secret.txt").parent.mkdir()
            (repo / ".agents/secret.txt").write_text("secret\n", encoding="utf-8")
            (repo / ".codex/config.toml").parent.mkdir()
            (repo / ".codex/config.toml").write_text("secret\n", encoding="utf-8")
            (repo / "viewer/dist/index.html").parent.mkdir()
            (repo / "viewer/dist/index.html").write_text("generated\n", encoding="utf-8")
            (repo / "scripts/cache/__pycache__/x.pyc").parent.mkdir(parents=True)
            (repo / "scripts/cache/__pycache__/x.pyc").write_bytes(b"x")
            egg_info = repo / "packages/meshscope/src/meshscope.egg-info/PKG-INFO"
            egg_info.parent.mkdir(parents=True)
            egg_info.write_text("generated metadata\n", encoding="utf-8")

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
            self.assertFalse(
                (stage / "packages/meshscope/src/meshscope.egg-info").exists()
            )

    def test_build_input_copy_does_not_follow_checkout_package_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            viewer = create_viewer_dependencies(root / "inputs")
            cad = create_cad_dependencies(root / "inputs")
            meshshot = create_meshshot_dependencies(root / "inputs")
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
                cvm_push.BuildInputs(viewer, cad, meshshot),
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
            self.assertTrue(
                (
                    stage
                    / "packages/meshshot/node_modules/three/package.json"
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
    def test_transfer_uses_one_unfiltered_rsync_from_exact_tree(self) -> None:
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
            self.assertFalse(
                any(arg.startswith("--exclude") for arg in argv), argv
            )
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

    def test_exact_transfer_tree_manifest_matches_real_rsync_filter(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            source = root / "source"
            repo = create_repo(root)
            root_viewer = source / "viewer/root.txt"
            nested_viewer = (
                source / "skills/cad-viewer/scripts/viewer/nested.txt"
            )
            for path in (root_viewer, nested_viewer):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
            nested_viewer.chmod(0o700)
            linked_modules = source / "packages/cadjs/node_modules"
            linked_modules.parent.mkdir(parents=True)
            os.symlink("../../viewer/node_modules", linked_modules)
            workflow = cvm_push.CvmPush(
                cvm_push.CommandRunner(),
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )
            transfer_tree = workflow.prepare_transfer_tree(source)
            target = root / "materialized"
            cvm_push._plugin_deployment.materialize_from_stage_manifest(
                transfer_tree,
                target,
                expected_manifest_digest=workflow.stage_manifest_digest,
            )

            self.assertFalse((target / "viewer").exists())
            self.assertFalse((transfer_tree / "packages/cadjs/node_modules").exists())
            self.assertFalse(
                (transfer_tree / "packages/cadjs/node_modules").is_symlink()
            )
            self.assertTrue(
                (target / "skills/cad-viewer/scripts/viewer/nested.txt").is_file()
            )
            self.assertEqual(
                stat.S_IMODE(
                    (transfer_tree / "skills/cad-viewer/scripts/viewer/nested.txt")
                    .stat()
                    .st_mode
                ),
                0o755,
            )

    def test_exact_transfer_tree_excludes_generated_egg_info(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            source = root / "source"
            metadata = source / "packages/meshscope/src/meshscope.egg-info/PKG-INFO"
            metadata.parent.mkdir(parents=True)
            metadata.write_text("generated metadata\n", encoding="utf-8")
            package_file = source / "packages/meshscope/src/meshscope/__init__.py"
            package_file.parent.mkdir(parents=True)
            package_file.write_text("# real package\n", encoding="utf-8")
            workflow = cvm_push.CvmPush(
                cvm_push.CommandRunner(),
                repo_root=REPO_ROOT,
                environ={"TMPDIR": str(root)},
            )

            transfer_tree = workflow.prepare_transfer_tree(source)
            manifest = json.loads(
                (transfer_tree / cvm_push._plugin_deployment.STAGE_MANIFEST_FILENAME)
                .read_text(encoding="utf-8")
            )
            materialized = root / "materialized"
            cvm_push._plugin_deployment.materialize_from_stage_manifest(
                transfer_tree,
                materialized,
                expected_manifest_digest=workflow.stage_manifest_digest,
            )
            listed = {entry["path"] for entry in manifest["entries"]}

            self.assertFalse(
                (transfer_tree / "packages/meshscope/src/meshscope.egg-info").exists()
            )
            self.assertNotIn(
                "packages/meshscope/src/meshscope.egg-info/PKG-INFO",
                listed,
            )
            self.assertIn("packages/meshscope/src/meshscope/__init__.py", listed)
            self.assertFalse(
                (materialized / "packages/meshscope/src/meshscope.egg-info").exists()
            )
            self.assertEqual(
                (
                    materialized
                    / "packages/meshscope/src/meshscope/__init__.py"
                ).read_text(encoding="utf-8"),
                "# real package\n",
            )

    def test_exact_transfer_tree_rejects_any_unmanifested_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            source = root / "source"
            source.mkdir()
            repo = create_repo(root)
            outside = root / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            os.symlink(outside, source / "unlisted-link")
            workflow = cvm_push.CvmPush(
                cvm_push.CommandRunner(),
                repo_root=repo,
                environ={"TMPDIR": str(root)},
            )

            with self.assertRaisesRegex(
                cvm_push.PushError,
                "unmanifested symlink",
            ) as error:
                workflow.prepare_transfer_tree(source)
            self.assertEqual(error.exception.status, 4)

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

    def test_remote_pilot_runtime_rebuilds_and_probes_native_meshscope(self) -> None:
        runner = FakeRunner()
        runner.respond("sha256sum", "")
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        workflow.verify_remote(cvm_push.RuntimeAttestation(hashes={}))

        self.assertEqual(len(runner.remote_commands), 3)
        install = runner.remote_commands[1]
        probe = runner.remote_commands[2]
        self.assertIn("cd ~/text-to-cad", install)
        self.assertIn("uv pip install", install)
        self.assertIn("--python .venv/bin/python", install)
        self.assertIn("--no-deps", install)
        self.assertIn("--reinstall", install)
        self.assertIn("--editable packages/meshscope", install)
        self.assertNotIn("--no-build-isolation", install)
        self.assertIn(">/dev/null 2>&1", install)
        self.assertIn("from meshscope.voxblame import _native", probe)

    def test_remote_pilot_runtime_reports_meshscope_install_failure(self) -> None:
        runner = FakeRunner()
        runner.respond("sha256sum", "")
        runner.respond(
            "uv pip install",
            stdout="native compiler failed",
            status=42,
        )
        workflow = cvm_push.CvmPush(
            runner, repo_root=REPO_ROOT, environ={}
        )

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.verify_remote(cvm_push.RuntimeAttestation(hashes={}))

        self.assertEqual(error.exception.status, 5)
        self.assertTrue(error.exception.transferred)
        self.assertEqual(
            str(error.exception),
            "CVM pilot Python runtime provisioning failed during meshscope install "
            "(classification=install-failed)",
        )
        self.assertEqual(len(runner.remote_commands), 2)

    def test_remote_pilot_runtime_rejects_output_as_a_classification_channel(
        self,
    ) -> None:
        runner = FakeRunner()
        runner.respond("sha256sum", "")
        runner.respond(
            "uv pip install",
            stdout=(
                "classification=python-runtime-unavailable\n"
                "/home/ubuntu/private/token.txt"
            ),
            status=42,
        )
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.verify_remote(cvm_push.RuntimeAttestation(hashes={}))

        message = str(error.exception)
        self.assertIn("classification=install-failed", message)
        self.assertNotIn("python-runtime-unavailable", message)
        self.assertNotIn("/home/ubuntu", message)
        self.assertNotIn("token.txt", message)

    def test_remote_install_command_uses_only_objective_exit_classes(self) -> None:
        runner = FakeRunner()
        runner.respond("sha256sum", "")
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})
        workflow.verify_remote(cvm_push.RuntimeAttestation(hashes={}))
        install_command = runner.remote_commands[1]

        cases = (
            ({"python_available": False, "uv_status": None}, 40),
            ({"uv_status": None}, 41),
            (
                {
                    "uv_output": (
                        "classification=python-runtime-unavailable\n"
                        "/home/ubuntu/private/token.txt"
                    )
                },
                42,
            ),
            ({"uv_status": 0, "uv_output": "ignored success output"}, 0),
        )
        for options, expected_status in cases:
            with self.subTest(options=options, status=expected_status):
                result = run_fake_remote_install(
                    install_command,
                    **options,
                )
                self.assertEqual(result.returncode, expected_status)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_remote_pilot_runtime_reports_native_probe_failure(self) -> None:
        runner = FakeRunner()
        runner.respond("sha256sum", "")
        runner.respond(
            "from meshscope.voxblame import _native",
            stdout="ImportError: incompatible module",
            status=1,
        )
        workflow = cvm_push.CvmPush(runner, repo_root=REPO_ROOT, environ={})

        with self.assertRaises(cvm_push.PushError) as error:
            workflow.verify_remote(cvm_push.RuntimeAttestation(hashes={}))

        self.assertEqual(error.exception.status, 5)
        self.assertTrue(error.exception.transferred)
        self.assertEqual(
            str(error.exception),
            "CVM pilot Python runtime provisioning failed during meshscope native probe",
        )

    def test_native_probe_output_stays_out_of_agent_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            fake_bin = root / "bin"
            remote_root = root / "remote" / "text-to-cad"
            probe_capture = root / "probe-capture"
            fake_bin.mkdir()
            make_executable(remote_root / ".venv/bin/python")
            (remote_root / ".venv/bin/python").write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'PRIVATE_NATIVE_PROBE_OUTPUT'\n"
                "printf '%s\\n' 'PRIVATE_NATIVE_PROBE_ERROR' >&2\n"
                "printf '%s\\n' 'PRIVATE_NATIVE_PROBE_OUTPUT' "
                ">\"$FAKE_PROBE_CAPTURE\"\n"
                "printf '%s\\n' 'PRIVATE_NATIVE_PROBE_ERROR' "
                ">>\"$FAKE_PROBE_CAPTURE\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            ssh = fake_bin / "ssh"
            ssh.write_text(
                "#!/bin/sh\n"
                "case \"$3\" in\n"
                "  *'test ! -e plugins'*) exit 0 ;;\n"
                "  *'uv pip install'*) exit 0 ;;\n"
                "esac\n"
                "HOME=\"$FAKE_REMOTE_HOME\" /bin/sh -c \"$3\"\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            driver = (
                "import importlib.util, os, sys\n"
                "from pathlib import Path\n"
                "module_path = Path(os.environ['CVM_PUSH_MODULE']).resolve()\n"
                "spec = importlib.util.spec_from_file_location("
                "'cvm_push_outer', module_path)\n"
                "assert spec is not None and spec.loader is not None\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "sys.modules[spec.name] = module\n"
                "spec.loader.exec_module(module)\n"
                "assert Path(module.__file__).resolve() == module_path\n"
                "workflow = module.CvmPush("
                "module.CommandRunner(), repo_root=Path(os.environ['REPO_ROOT']), "
                "environ={'TMPDIR': os.environ['CVM_PUSH_TMPDIR']}, agent=True)\n"
                "workflow.run = lambda: workflow.verify_remote("
                "module.RuntimeAttestation(hashes={}))\n"
                "raise SystemExit(module.execute(workflow))\n"
            )
            env = {
                **{
                    key: value
                    for key, value in os.environ.items()
                    if key != "PYTHONPATH"
                },
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "FAKE_REMOTE_HOME": str(root / "remote"),
                "FAKE_PROBE_CAPTURE": str(probe_capture),
                "CVM_PUSH_MODULE": str(MODULE_PATH.resolve()),
                "CVM_PUSH_TMPDIR": str(root),
                "REPO_ROOT": str(REPO_ROOT),
            }
            result = subprocess.run(
                [sys.executable, "-c", driver],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            receipt = json.loads(result.stdout.splitlines()[-1])
            persisted = Path(receipt["receipt_path"]).read_text(encoding="utf-8")
            log = Path(receipt["log_path"]).read_text(encoding="utf-8")
            private_capture = probe_capture.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            private_capture,
            "PRIVATE_NATIVE_PROBE_OUTPUT\nPRIVATE_NATIVE_PROBE_ERROR\n",
        )
        self.assertEqual(
            receipt["error"],
            "CVM pilot Python runtime provisioning failed during meshscope "
            "native probe",
        )
        for boundary in (result.stdout, result.stderr, persisted, log):
            self.assertNotIn("PRIVATE_NATIVE_PROBE_OUTPUT", boundary)
            self.assertNotIn("PRIVATE_NATIVE_PROBE_ERROR", boundary)

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
    def test_bundle_stage_reuses_the_validated_snapshot_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            stage = root / "stage"
            stage.mkdir()
            runner = FakeRunner()
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=repo,
                environ={"KEEP": "value"},
            )

            workflow.bundle_stage(stage)

        self.assertEqual(runner.stream_status, 0)
        self.assertEqual(len(runner.stream_envs), 1)
        env = runner.stream_envs[0]
        assert env is not None
        expected = str(stage / "tmp/cad-snapshot-build")
        self.assertEqual(env["KEEP"], "value")
        for name in (
            "CAD_SNAPSHOT_BUILD_DEPS_DIR",
            "DXF_SNAPSHOT_BUILD_DEPS_DIR",
            "SDF_SNAPSHOT_BUILD_DEPS_DIR",
            "SRDF_SNAPSHOT_BUILD_DEPS_DIR",
            "URDF_SNAPSHOT_BUILD_DEPS_DIR",
            "NODE_BUILDER_BUILD_DEPS_DIR",
        ):
            self.assertEqual(env[name], expected)

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
            inputs = cvm_push.BuildInputs(
                root / "viewer", root / "cad", root / "meshshot"
            )
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
            workflow.prepare_transfer_tree = lambda stage: (
                events.append("prepare-transfer"),
                stage,
            )[1]
            workflow.transfer_stage = lambda stage: events.append("transfer")
            workflow.remove_legacy_plugin_tree = lambda: events.append("cleanup-plugin")
            workflow.verify_remote = lambda expected: events.append("verify")
            workflow.install_plugin_authority = lambda _attestation: (
                events.append("install"),
                {"schema": "text-to-cad.plugin-authority/2"},
            )[1]
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
                    "prepare-transfer",
                    "transfer",
                    "cleanup-plugin",
                    "verify",
                    "install",
                ],
            )


class InstallPluginAuthorityTests(unittest.TestCase):
    """Cover the SSH-hosted install/verify/publish-authority phase."""

    def _authority_payload(self) -> dict:
        return {
            "schema": "text-to-cad.plugin-authority/2",
            "deployment_id": "d" * 64,
            "version": "0.4.21",
            "plugin_selector": "cad@text-to-cad",
            "prepared_manifest_digest": "a" * 64,
            "installed_manifest_digest": "a" * 64,
            "codex_version": "codex-cli 0.147.0",
            "published_at": "2026-08-23T00:00:00Z",
            "source_git_sha": "deadbeef" * 5,
            "deployment_dir": "/home/pilot/.text-to-cad-codex/deployments/" + "d" * 64,
            "publish_tree": "/home/pilot/.text-to-cad-codex/deployments/" + "d" * 64 + "/publish-tree",
            "codex_home": "/home/pilot/.text-to-cad-codex/deployments/" + "d" * 64 + "/codex-home",
            "installed_path": "/home/pilot/.text-to-cad-codex/deployments/" + "d" * 64 + "/codex-home/plugins/cache/cad",
            "critical_runtimes": [],
            "transfer_provenance": {
                "schema": "text-to-cad.push-provenance/2",
                "mac_branch": "develop",
                "mac_head": "0" * 40,
                "mac_state": "clean",
                "stage_manifest_digest": "0" * 64,
            },
        }

    def _workflow(self, root: Path, runner: FakeRunner) -> "cvm_push.CvmPush":
        workflow = cvm_push.CvmPush(
            runner,
            repo_root=create_repo(root),
            environ={"TMPDIR": str(root)},
        )
        workflow.source = cvm_push.SourceProvenance(
            branch="develop", head="0" * 40, state="clean"
        )
        # The stage manifest is authored between ``attest`` and ``transfer``
        # in the real workflow; unit tests exercising install-authority skip
        # the earlier phases, so we pin a stable digest directly.
        workflow.stage_manifest_digest = "0" * 64
        return workflow

    @staticmethod
    def _attestation() -> "cvm_push.RuntimeAttestation":
        return cvm_push.RuntimeAttestation(
            hashes={
                path: "b" * 64
                for path in cvm_push._plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS
            }
        )

    def test_install_success_embeds_authority_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            payload = self._authority_payload()
            runner.respond("cvm_install_plugin.py", json.dumps(payload))
            workflow = self._workflow(root, runner)
            embedded = workflow.install_plugin_authority(self._attestation())
        self.assertEqual(embedded, payload)
        self.assertEqual(workflow.plugin_authority, payload)
        self.assertEqual(len(runner.remote_commands), 1)
        command = runner.remote_commands[0]
        # Command must invoke the CVM helper explicitly with argv (not shell-composed
        # from untrusted paths) and pass the fixed HOME-rooted authority root plus
        # the encoded provenance blob.
        self.assertIn("cvm_install_plugin.py", command)
        self.assertIn('remote_root="$HOME/text-to-cad"', command)
        self.assertIn(
            'python3 "$remote_root/scripts/pilot/cvm_install_plugin.py"', command
        )
        self.assertIn('--transferred-source "$remote_root"', command)
        self.assertNotIn("~/text-to-cad/scripts/pilot/cvm_install_plugin.py", command)
        self.assertIn("--codex-home-root", command)
        self.assertIn("--provenance-b64", command)

    def test_install_command_transports_provenance_blob(self) -> None:
        import base64 as _b64
        import shlex as _shlex

        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            payload = self._authority_payload()
            runner.respond("cvm_install_plugin.py", json.dumps(payload))
            workflow = self._workflow(root, runner)
            workflow.install_plugin_authority(self._attestation())
            command = runner.remote_commands[0]
        tokens = _shlex.split(command)
        idx = tokens.index("--provenance-b64")
        encoded = tokens[idx + 1]
        decoded = json.loads(_b64.urlsafe_b64decode(encoded.encode("ascii")).decode())
        self.assertEqual(decoded["schema"], "text-to-cad.push-provenance/2")
        self.assertEqual(decoded["mac_branch"], "develop")
        self.assertEqual(decoded["mac_head"], "0" * 40)
        self.assertEqual(decoded["mac_state"], "clean")
        self.assertEqual(decoded["stage_manifest_digest"], "0" * 64)
        self.assertEqual(
            decoded["runtime_attestation"],
            {
                path: "b" * 64
                for path in cvm_push._plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS
            },
        )

    def test_install_before_stage_manifest_fails_closed(self) -> None:
        # Regression: install-authority must refuse to build a provenance
        # blob when no stage manifest digest has been recorded — otherwise
        # the CVM publisher would materialize publish-tree-src from a
        # persistent overlay it cannot bound, silently entering deployment
        # identity.
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
            )
            workflow.source = cvm_push.SourceProvenance(
                branch="develop", head="0" * 40, state="clean"
            )
            # Note: stage_manifest_digest deliberately left as None.
            with self.assertRaises(cvm_push.PushError) as ctx:
                workflow.install_plugin_authority(self._attestation())
            self.assertEqual(ctx.exception.status, cvm_push.INSTALL_EXIT_CODE)
            self.assertTrue(ctx.exception.transferred)
            self.assertIn("stage manifest", str(ctx.exception).lower())

    def test_install_before_source_inspection_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=create_repo(root),
                environ={"TMPDIR": str(root)},
            )
            with self.assertRaises(cvm_push.PushError) as ctx:
                workflow.install_plugin_authority(self._attestation())
            self.assertEqual(ctx.exception.status, cvm_push.INSTALL_EXIT_CODE)

    def test_install_failure_maps_to_exit_code_7(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            error_payload = {
                "schema": "text-to-cad.plugin-authority-error/1",
                "stage": "install",
                "error": "codex plugin add failed",
            }
            runner.respond(
                "cvm_install_plugin.py", json.dumps(error_payload), status=1
            )
            workflow = self._workflow(root, runner)
            with self.assertRaises(cvm_push.PushError) as ctx:
                workflow.install_plugin_authority(self._attestation())
            self.assertEqual(ctx.exception.status, cvm_push.INSTALL_EXIT_CODE)
            self.assertTrue(ctx.exception.transferred)
            self.assertIn("codex plugin add failed", str(ctx.exception))

    def test_verify_failure_maps_to_exit_code_8(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            error_payload = {
                "schema": "text-to-cad.plugin-authority-error/1",
                "stage": "verify",
                "error": "installed cache does not match prepared publish tree",
            }
            runner.respond(
                "cvm_install_plugin.py", json.dumps(error_payload), status=1
            )
            workflow = self._workflow(root, runner)
            with self.assertRaises(cvm_push.PushError) as ctx:
                workflow.install_plugin_authority(self._attestation())
            self.assertEqual(ctx.exception.status, cvm_push.VERIFY_EXIT_CODE)
            self.assertTrue(ctx.exception.transferred)

    def test_missing_authority_payload_fails_verify(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runner = FakeRunner()
            runner.respond("cvm_install_plugin.py", "", status=0)
            workflow = self._workflow(root, runner)
            with self.assertRaises(cvm_push.PushError) as ctx:
                workflow.install_plugin_authority(self._attestation())
            self.assertEqual(ctx.exception.status, cvm_push.VERIFY_EXIT_CODE)
            self.assertTrue(ctx.exception.transferred)


class StageExclusionTests(unittest.TestCase):
    def test_shipped_cvmignore_excludes_node_modules_symlink_leaves(self) -> None:
        rules = {
            line.strip()
            for line in (REPO_ROOT / ".cvmignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("node_modules", rules)
        self.assertIn("*.egg-info/", rules)
        self.assertIn("*.egg-info/", cvm_push.STAGE_SOURCE_EXCLUDES)

    def test_stage_source_excludes_the_local_authority_root(self) -> None:
        # The CVM-published plugin authority must never rsync back into
        # Mac -> CVM staging: it is CVM-owned deployment state.
        self.assertIn("/.text-to-cad-codex/", cvm_push.STAGE_SOURCE_EXCLUDES)


if __name__ == "__main__":
    unittest.main()
