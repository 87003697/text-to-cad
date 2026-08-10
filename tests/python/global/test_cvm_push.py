from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        if relative.endswith("browsers.json"):
            path.write_text(
                json.dumps(
                    {
                        "browsers": [
                            {
                                "name": "chromium-headless-shell",
                                "revision": "1234",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
        else:
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


class BuildInputTests(unittest.TestCase):
    def test_incomplete_worktree_inputs_fall_back_to_complete_primary(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            repo = create_repo(root)
            primary = root / "primary"
            viewer = create_viewer_dependencies(primary)
            cad = create_cad_dependencies(primary)
            (repo / "viewer/node_modules/playwright").mkdir(parents=True)
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
            implicitjs_link = stage / "viewer/node_modules/implicitjs"
            self.assertTrue(cadjs_link.is_symlink())
            self.assertEqual(os.readlink(cadjs_link), "../packages/cadjs")
            self.assertTrue(implicitjs_link.is_symlink())
            self.assertEqual(os.readlink(implicitjs_link), "../packages/implicitjs")
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
            self.assertEqual(attestation.chromium_revision, "1234")

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
    def test_transfer_uses_one_rsync_with_include_before_exclude(self) -> None:
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
            include = f"--include={cvm_push.IMPLICIT_NODE_MODULES_INCLUDE}"
            exclude = f"--exclude-from={repo.resolve() / '.cvmignore'}"
            self.assertLess(argv.index(include), argv.index(exclude))
            self.assertEqual(argv[-2], f"{stage}/")
            self.assertEqual(argv[-1], cvm_push.REMOTE_DESTINATION)

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

    def test_real_rsync_filter_includes_nested_runtime_and_excludes_root_viewer(
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
            nested_dependency = (
                source
                / "skills/implicit-cad/scripts/packages/implicitjs/"
                "node_modules/playwright/package.json"
            )
            for path in (root_viewer, nested_viewer, nested_dependency):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
            target.mkdir()

            result = subprocess.run(
                [
                    "rsync",
                    "-a",
                    f"--include={cvm_push.IMPLICIT_NODE_MODULES_INCLUDE}",
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
            self.assertTrue(
                (
                    target
                    / "skills/implicit-cad/scripts/packages/implicitjs/"
                    "node_modules/playwright/package.json"
                ).is_file()
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
            runner.respond("chromium_headless_shell-1234", "")
            workflow = cvm_push.CvmPush(
                runner,
                repo_root=stage,
                environ={},
            )

            workflow.verify_remote(attestation)
            command = runner.remote_commands[0]
            for relative in cvm_push.PRODUCTION_RUNTIME.physical_directories:
                self.assertIn(f"test ! -L {relative}", command)
            for relative in cvm_push.PRODUCTION_RUNTIME.required_files:
                self.assertIn(f"test -f {relative}", command)
            self.assertIn("chromium_headless_shell-1234", runner.remote_commands[1])

            bad = dict(attestation.hashes)
            first = next(iter(bad))
            bad[first] = "0" * 64
            with self.assertRaisesRegex(cvm_push.PushError, "hash mismatch") as error:
                workflow.verify_remote(
                    cvm_push.RuntimeAttestation(
                        hashes=bad,
                        chromium_revision="1234",
                    )
                )
            self.assertEqual(error.exception.status, 5)


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
            attestation = cvm_push.RuntimeAttestation(
                hashes={},
                chromium_revision="1234",
            )
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
                    "verify",
                ],
            )


if __name__ == "__main__":
    unittest.main()
