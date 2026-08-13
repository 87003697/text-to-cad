from __future__ import annotations

import errno
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.pilot import deployment_authority
from scripts.pilot import provider_free_runner
from scripts.pilot import provider_free_scenarios
from scripts.pilot.cvm_job import __main__ as cvm_job_cli
from scripts.pilot.cvm_job import protocol, runtime
from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"
SUBMIT_SCRIPT = PILOT_ROOT / "cvm-submit.sh"
MONITOR_SCRIPT = PILOT_ROOT / "cvm-monitor.sh"
MONITOR_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-monitor" / "SKILL.md"
SUBMIT_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-submit" / "SKILL.md"


class CvmJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary_directory(prefix="cvm-job-")
        self.root_text = self.temporary.__enter__()
        self.workspace = Path(self.root_text)
        self.state_root = self.workspace / ".cvm-jobs"
        self.repo_root = self.workspace / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "outputs").mkdir()
        for declared in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            path = self.repo_root / declared
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{declared}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "authority-marker.txt").write_text(
                    f"{declared}\n", encoding="utf-8"
                )
        cadpy = self.repo_root / deployment_authority.CADPY_RUNTIME_PATH
        cadpy.parent.mkdir(parents=True, exist_ok=True)
        cadpy.write_text("cadpy\n", encoding="utf-8")
        browser_cache = self.workspace / "provider-home/.cache/ms-playwright"
        browser = browser_cache / (
            "chromium_headless_shell-1234/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"trusted chromium")
        browser.chmod(0o755)
        runtime_identity = {
            "schema": "cvm.provider-free-runtime-identity/1",
            "bwrap": {
                "path": "/usr/bin/bwrap",
                "sha256": "b" * 64,
                "version": "bubblewrap 1.2.3",
            },
            "chromium": {
                "revision": "1234",
                "host_cache_path": os.fspath(browser_cache),
                "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                "executable_path": os.fspath(browser),
                "sha256": hashlib.sha256(browser.read_bytes()).hexdigest(),
            },
            "cadpy": {
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
            },
        }
        deployment_authority.write_receipt(
            self.repo_root,
            source_head="a" * 40,
            runtime_identity=runtime_identity,
        )
        self.repo_patch = mock.patch.object(runtime, "REPO_ROOT", self.repo_root)
        self.repo_patch.start()
        self.group = "20260805-170000-audit"

    def tearDown(self) -> None:
        self.repo_patch.stop()
        self.temporary.__exit__(None, None, None)

    def _browser_identity(self) -> tuple[dict[str, object], Path]:
        host_cache = self.workspace / "provider-home/.cache/ms-playwright"
        executable = host_cache / (
            "chromium_headless_shell-1234/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"deployment-attested chromium")
        executable.chmod(0o755)
        (executable.parents[1] / "resources.pak").write_bytes(b"resource")
        return (
            {
                "revision": "1234",
                "host_cache_path": os.fspath(host_cache),
                "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                "executable_path": os.fspath(executable),
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            },
            executable,
        )

    def test_attested_browser_stage_copies_exact_revision_and_cleans_on_success(
        self,
    ) -> None:
        chromium, executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with runtime.staged_attested_browser(
            chromium,
            handle,
            repo_root=self.repo_root,
        ) as mount:
            self.assertFalse(mount.host_revision.is_relative_to(self.repo_root))
            self.assertEqual("attested", mount.host_revision.name)
            staged_executable = mount.host_revision / (
                "chrome-headless-shell-linux64/chrome-headless-shell"
            )
            self.assertEqual(executable.read_bytes(), staged_executable.read_bytes())
            self.assertEqual(
                protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE,
                mount.sandbox_cache,
            )
            stage_root = mount.host_revision.parent
            self.assertTrue(stage_root.is_dir())
            self.assertEqual(
                ["attested"],
                [path.name for path in stage_root.iterdir()],
            )
            self.assertEqual(
                executable.stat().st_dev,
                mount.host_revision.stat().st_dev,
            )

        self.assertFalse(stage_root.exists())

    def test_attested_browser_stage_rejects_kernel_noexec_probe(self) -> None:
        chromium, _executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with (
            mock.patch.object(
                runtime,
                "_run_browser_stage_exec_probe",
                side_effect=PermissionError(
                    errno.EACCES,
                    "injected noexec mount",
                ),
            ) as run,
            self.assertRaisesRegex(
                runtime.BrowserStageError,
                "exec-permitted",
            ),
        ):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

        run.assert_called_once()
        stage_parent = (
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
        )
        self.assertFalse(stage_parent.exists())

    def test_attested_browser_stage_cleans_after_subprocess_sigterm(self) -> None:
        chromium, _executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"
        stage_root = (
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
            / f"{self.group}.20260812-100000-issue15-runtime-authority"
        )
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import json,os,sys,time\n"
                    "from pathlib import Path\n"
                    "from scripts.pilot.cvm_job import runtime\n"
                    "identity=json.loads(sys.argv[1])\n"
                    "with runtime.staged_attested_browser("
                    "identity,sys.argv[2],repo_root=Path(sys.argv[3])):\n"
                    " os.write(1,b'READY\\n')\n"
                    " time.sleep(30)\n"
                ),
                json.dumps(chromium, sort_keys=True),
                handle,
                os.fspath(self.repo_root),
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None
            ready = child.stdout.readline()
            if ready != "READY\n":
                detail = child.stderr.read() if child.stderr is not None else ""
                self.fail(f"stage child did not become ready: {ready!r} {detail}")
            self.assertTrue(stage_root.is_dir())
            child.send_signal(signal.SIGTERM)
            self.assertEqual(-signal.SIGTERM, child.wait(timeout=10))
            self.assertFalse(stage_root.exists())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()

    def test_attested_browser_stage_rejects_wrong_executable_digest(self) -> None:
        chromium, _executable = self._browser_identity()
        chromium["sha256"] = "0" * 64
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    def test_attested_browser_stage_rejects_symlink_entry(self) -> None:
        chromium, executable = self._browser_identity()
        (executable.parents[1] / "linked-resource").symlink_to("resources.pak")
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX special files")
    def test_attested_browser_stage_rejects_special_entry(self) -> None:
        chromium, executable = self._browser_identity()
        os.mkfifo(executable.parents[1] / "browser.pipe")
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    def test_attested_browser_stage_rejects_non_executable_browser(self) -> None:
        chromium, executable = self._browser_identity()
        executable.chmod(0o644)
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    def test_attested_browser_stage_rejects_wrong_revision(self) -> None:
        chromium, _executable = self._browser_identity()
        chromium["revision"] = "9999"
        chromium["executable_path"] = chromium["executable_path"].replace(
            "chromium_headless_shell-1234",
            "chromium_headless_shell-9999",
        )
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    def test_attested_browser_stage_rejects_collision_without_deleting_owner(
        self,
    ) -> None:
        chromium, _executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with runtime.staged_attested_browser(
            chromium,
            handle,
            repo_root=self.repo_root,
        ) as owner:
            with self.assertRaises(runtime.BrowserStageError):
                with runtime.staged_attested_browser(
                    chromium,
                    handle,
                    repo_root=self.repo_root,
                ):
                    pass
            self.assertTrue(owner.host_revision.is_dir())

        self.assertFalse(owner.host_revision.parent.exists())

    def test_attested_browser_stage_rejects_symlinked_stage_parent(self) -> None:
        chromium, _executable = self._browser_identity()
        stage_parent = (
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
        )
        outside = self.workspace / "untrusted-stage"
        outside.mkdir()
        stage_parent.symlink_to(outside, target_is_directory=True)
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with self.assertRaises(runtime.BrowserStageError):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

        self.assertEqual([], list(outside.iterdir()))

    def test_attested_browser_stage_removes_partial_copy(self) -> None:
        chromium, _executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"
        real_copy = shutil.copy2
        copied = 0

        def fail_after_one_file(source, destination, *args, **kwargs):
            nonlocal copied
            copied += 1
            if copied > 1:
                raise OSError("injected partial copy")
            return real_copy(source, destination, *args, **kwargs)

        with (
            mock.patch.object(
                runtime.shutil,
                "copy2",
                side_effect=fail_after_one_file,
            ),
            self.assertRaises(runtime.BrowserStageError),
        ):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

        stage_parent = (
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
        )
        self.assertFalse(stage_parent.exists())

    def test_attested_browser_stage_rejects_incomplete_or_changed_copy(self) -> None:
        chromium, _executable = self._browser_identity()
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"
        real_copy = shutil.copy2

        def corrupt_resource(source, destination, *args, **kwargs):
            result = real_copy(source, destination, *args, **kwargs)
            if Path(source).name == "resources.pak":
                Path(destination).write_bytes(b"corrupted")
            return result

        with (
            mock.patch.object(
                runtime.shutil,
                "copy2",
                side_effect=corrupt_resource,
            ),
            self.assertRaises(runtime.BrowserStageError),
        ):
            with runtime.staged_attested_browser(
                chromium,
                handle,
                repo_root=self.repo_root,
            ):
                pass

    def test_attested_browser_stage_cleans_after_every_context_exit_class(
        self,
    ) -> None:
        for index, raised in enumerate(
            (RuntimeError("failure"), KeyboardInterrupt(), SystemExit(23)),
            start=1,
        ):
            with self.subTest(exit_type=type(raised).__name__):
                chromium, _executable = self._browser_identity()
                handle = (
                    f"{self.group}/"
                    f"20260812-10000{index}-issue15-runtime-authority"
                )
                with self.assertRaises(type(raised)):
                    with runtime.staged_attested_browser(
                        chromium,
                        handle,
                        repo_root=self.repo_root,
                    ) as mount:
                        stage_root = mount.host_revision.parent
                        raise raised
                self.assertFalse(stage_root.exists())

    def test_attested_browser_stage_cleans_read_only_runtime_directories(self) -> None:
        chromium, executable = self._browser_identity()
        executable.parent.chmod(0o555)
        executable.parents[1].chmod(0o555)
        handle = f"{self.group}/20260812-100000-issue15-runtime-authority"

        with runtime.staged_attested_browser(
            chromium,
            handle,
            repo_root=self.repo_root,
        ) as mount:
            stage_root = mount.host_revision.parent

        self.assertFalse(stage_root.exists())

    def test_provider_free_sandbox_preserves_only_nested_bwrap_setup_capabilities(
        self,
    ) -> None:
        exp_dir = self.repo_root / "outputs" / self.group / "nested-bwrap-contract"
        exp_dir.mkdir(parents=True)
        receipt = json.loads(
            (self.repo_root / deployment_authority.RECEIPT_PATH).read_bytes()
        )

        argv = runtime.provider_free_sandbox_argv(
            "issue15-runtime-authority",
            exp_dir,
            receipt["runtime_identity"],
            repo_root=self.repo_root,
        )

        executable_tmpfs = argv.index("--tmpfs", argv.index("--tmpfs") + 1)
        self.assertEqual(
            ["--tmpfs", "/meshshot-exec"],
            argv[executable_tmpfs : executable_tmpfs + 2],
        )
        supervisor_tmpfs = argv.index("--tmpfs", executable_tmpfs + 1)
        self.assertEqual(
            ["--tmpfs", "/meshshot-supervisor"],
            argv[supervisor_tmpfs : supervisor_tmpfs + 2],
        )
        self.assertEqual(
            "/meshshot-exec",
            runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT[
                "MESHSHOT_EXECUTABLE_ROOT"
            ],
        )

        self.assertEqual(
            ["user", "network", "pid", "ipc", "uts"],
            runtime.PROVIDER_FREE_SANDBOX_PROFILE["namespaces"],
        )
        self.assertEqual(
            {
                "baseline": "drop-all",
                "retained": [
                    "CAP_SYS_ADMIN",
                    "CAP_SYS_CHROOT",
                    "CAP_NET_ADMIN",
                    "CAP_SETUID",
                    "CAP_SETGID",
                    "CAP_SYS_PTRACE",
                    "CAP_SETFCAP",
                ],
                "scope": "outer-user-namespace",
                "purpose": "nested-bwrap-setup",
            },
            runtime.PROVIDER_FREE_SANDBOX_PROFILE["capabilities"],
        )
        self.assertEqual(
            {
                "capabilities": "drop-all",
                "mount_namespace": "inherit-outer",
                "receipt": protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
                "browser_identity_diagnostic": {
                    "schema": (
                        protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
                    ),
                    "receipt": (
                        protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
                    ),
                    "operation": "preview_browser_identity",
                    "substages": [
                        "private_snapshot_launch_image_identity",
                        "live_running_image_identity",
                        "loopback_listener_address_ownership",
                        "connected_cdp_browser_version_identity",
                        "runtime_evidence_cross_binding",
                    ],
                    "private_snapshot_phases": sorted(
                        protocol.PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                    ),
                    "playwright_package_revision_checks": sorted(
                        protocol.PROVIDER_FREE_PLAYWRIGHT_PACKAGE_REVISION_CHECKS
                    ),
                    "private_version_execution_checks": sorted(
                        protocol.PROVIDER_FREE_PRIVATE_VERSION_EXECUTION_CHECKS
                    ),
                    "binding": [
                        protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                        "artifact_manifest.json",
                    ],
                    "published": "first-failing-closed-substage-only",
                },
                "public_wrapper": {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "receipt": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
                    "operations": sorted(
                        protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS
                    ),
                    "published": "closed-operation-only-no-process-data",
                    "publication_failure": {
                        "operation": (
                            "preview_public_wrapper_evidence_publication"
                        ),
                        "scenario_failure": "run/scenario-failure.json",
                        "terminal_manifest": "artifact_manifest.json",
                        "wrapper_receipt": "absent",
                    },
                },
            },
            runtime.PROVIDER_FREE_SANDBOX_PROFILE["preview_process"],
        )
        self.assertEqual(
            {
                "source": "deployment-attested-host-revision",
                "source_filesystem": "same-device-as-deployment-browser",
                "scope": "single-attested-revision",
                "destination": protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE,
                "staged_revision": "attested",
                "staged_executable": (
                    protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
                ),
                "destination_filesystem": (
                    "read-only-bind-of-exec-permitted-host-stage"
                ),
                "tree_validation": "regular-files-only-no-links-or-special",
                "executable_validation": {
                    "sha256": "deployment-runtime-identity",
                    "execute_bits": "required",
                },
                "exec_permission_validation": {
                    "mechanism": (
                        "kernel-execve-repository-owned-immediate-exit-probe"
                    ),
                    "network": "none",
                    "timeout_seconds": 5,
                    "expected_stdout": "cvm.browser-stage-exec-probe/1",
                },
                "sandbox_exec_diagnostics": {
                    "schema": (
                        protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA
                    ),
                    "receipt": (
                        protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
                    ),
                    "executable": (
                        protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
                    ),
                    "argv_suffix": ["--version"],
                    "lifecycle": "non-rendering-immediate-exit",
                    "environment_names": ["HOME", "LANG", "PATH"],
                    "network": "none",
                    "timeout_seconds": 5,
                    "node_probe": "retired-by-python-prelaunch",
                    "result": {
                        "exit_code": 0,
                        "stdout": "single-chromium-version-line",
                        "stdout_max_bytes": 128,
                        "stderr": "empty",
                    },
                    "seams": [
                        "outer-python-direct",
                        "outer-supervised-python-prelaunch",
                        "fixed-unix-authority",
                        "nested-playwright-loopback-cdp-attach",
                    ],
                    "published": "closed-outcomes-only-no-raw-output",
                    "cleanup": "no-profile-or-persistent-process-artifacts",
                },
                "nested_mount": "read-only-exact-staged-cache",
                "launch_handoff": {
                    "environment": "MESHSHOT_BROWSER_EXECUTABLE",
                    "value": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                    "validation": "absolute-regular-non-symlink-executable",
                    "launch_owner": "outer-trusted-browser-supervisor",
                    "playwright_option": "connect_over_cdp-is-local",
                },
                "cleanup": "supervisor-context-terminal-all-exit-classes",
                "catchable_signal_cleanup": ["SIGINT", "SIGTERM"],
                "uncatchable_termination": (
                    "stale-stage-collision-fail-closed"
                ),
            },
            runtime.PROVIDER_FREE_SANDBOX_PROFILE["browser_runtime_staging"],
        )
        self.assertEqual(
            128 * 1024**3,
            runtime.PROVIDER_FREE_SANDBOX_PROFILE["resource_limits"][
                "address_space_bytes"
            ],
        )
        self.assertEqual(
            {
                "profile": "cad.canonical-build-worker/2",
                "address_space": {
                    "platform": "linux",
                    "soft_bytes": 16 * 1024**3,
                    "hard_bytes": 16 * 1024**3,
                },
            },
            runtime.PROVIDER_FREE_SANDBOX_PROFILE[
                "untrusted_canonical_worker"
            ],
        )
        self.assertIn("--unshare-user", argv)
        chromium = receipt["runtime_identity"]["chromium"]
        expected_host_revision = (
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
            / f"{self.group}.nested-bwrap-contract"
            / "attested"
        )
        browser_mounts = [
            argv[index : index + 3]
            for index, value in enumerate(argv[:-2])
            if value == "--ro-bind"
            and argv[index + 2]
            == f"{protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested"
        ]
        self.assertEqual(
            [
                [
                    "--ro-bind",
                    os.fspath(expected_host_revision),
                    f"{protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested",
                ]
            ],
            browser_mounts,
        )
        self.assertNotIn(
            [
                "--ro-bind",
                chromium["host_cache_path"],
                chromium["sandbox_cache_path"],
            ],
            [argv[index : index + 3] for index in range(len(argv) - 2)],
        )
        self.assertEqual("ALL", argv[argv.index("--cap-drop") + 1])
        self.assertEqual(
            [
                "CAP_SYS_ADMIN",
                "CAP_SYS_CHROOT",
                "CAP_NET_ADMIN",
                "CAP_SETUID",
                "CAP_SETGID",
                "CAP_SYS_PTRACE",
                "CAP_SETFCAP",
            ],
            [
                argv[index + 1]
                for index, value in enumerate(argv[:-1])
                if value == "--cap-add"
            ],
        )

    def submit(self) -> str:
        def fake_detach(handle, command, root):
            self.detached = (handle, list(command), root)
            return 1234

        result = runtime.submit_pilot(
            "airplane",
            self.group,
            state_root=self.state_root,
            detach=fake_detach,
        )
        return result["job"]

    def write_manifest(self, handle: str, final_status: object) -> None:
        path = self.repo_root / "outputs" / handle / "artifact_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"final_status": final_status}), encoding="utf-8")

    def write_provider_free_terminal_evidence(
        self,
        handle: str,
        *,
        complete: bool = True,
        stripped: list[str] | None = None,
    ) -> None:
        state = protocol.load_state(self.state_root, handle)
        exp_dir = self.repo_root / "outputs" / handle
        exp_dir.mkdir(parents=True, exist_ok=True)
        proof = {
            "schema": "cvm.provider-free-execution/1",
            "job": handle,
            "scenario": state["scenario"],
            "execution_profile": state["execution_profile"],
            "request_authority": {
                "sha256": state["request_authority_sha256"],
                "deployment_tree_sha256": state["request_authority"][
                    "deployment_tree_sha256"
                ],
                "immutable_request": protocol.request_authority_payload(state),
            },
            "sandbox": {
                "network": "isolated-loopback",
                "resource_profile": state["execution_profile"]["id"],
            },
            "provider_environment": {
                "allowlist": ["HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ"],
                "stripped": stripped
                if stripped is not None
                else [
                    "ANTHROPIC_API_KEY",
                    "HTTPS_PROXY",
                    "OPENAI_API_KEY",
                    "VENUS_TOKEN",
                ],
                "credential_values_recorded": False,
            },
            "requests": {"model_gateway": 0, "provider": 0, "tap": 0},
            "sandbox_enforcement": {
                "path": "run/sandbox-enforcement.json",
                "sha256": "",
            },
        }
        proof_path = exp_dir / "run" / "provider-free-execution.json"
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        files = []
        if complete:
            deployed_receipt_path = self.repo_root / ".cvm-deployment.json"
            deployed_receipt = json.loads(deployed_receipt_path.read_bytes())
            deployment_authority.materialize_receipt(
                self.repo_root,
                deployed_receipt,
                exp_dir / "run/deployed-source",
            )
            (exp_dir / "run/deployed-source-authority.json").write_bytes(
                deployed_receipt_path.read_bytes()
            )
            runtime_identity = deployed_receipt["runtime_identity"]
            (exp_dir / "run/sandbox-enforcement.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm.provider-free-sandbox-enforcement/1",
                        "network": "isolated-loopback",
                        "argv": runtime.provider_free_sandbox_argv(
                            state["scenario"]["name"],
                            exp_dir,
                            runtime_identity,
                        ),
                        "environment_names": [
                            "HOME",
                            "LANG",
                            "PATH",
                            "PLAYWRIGHT_BROWSERS_PATH",
                            "MESHSHOT_EXECUTABLE_ROOT",
                            "MESHSHOT_BROWSER_RUNTIME_MODE",
                            "PYTHONDONTWRITEBYTECODE",
                            "TZ",
                        ],
                        "required_environment": runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT,
                        "sandbox_profile": runtime.PROVIDER_FREE_SANDBOX_PROFILE,
                        "runtime_identity": runtime_identity,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            sandbox_bytes = (exp_dir / "run/sandbox-enforcement.json").read_bytes()
            proof["sandbox_enforcement"]["sha256"] = hashlib.sha256(
                sandbox_bytes
            ).hexdigest()
            (exp_dir / protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH).write_text(
                json.dumps(
                    {
                        "schema": protocol.PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
                        "argv": protocol.provider_free_preview_sandbox_argv(
                            state["group"], state["exp"]
                        ),
                        "capabilities": "drop-all",
                        "mount_namespace": "inherit-outer",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            (
                exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            ).write_text(
                json.dumps(
                    {
                        "schema": (
                            protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA
                        ),
                        "executable": (
                            protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
                        ),
                        "probe": "chromium-version-immediate-exit",
                        "outer": "passed",
                        "nested": "not-run",
                        "node_attached": "not-run",
                        "node_detached": "not-run",
                        "node_failure_kind": "not-run",
                        "prelaunched_cdp": "passed",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            (
                exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            ).write_text(
                json.dumps(
                    {
                        "schema": (
                            protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA
                        ),
                        "operation": "passed",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            for name, data in (
                ("run/runtime-authority-smoke.json", b"{}\n"),
                ("workspace-authority.json", b"{}\n"),
                ("workspace-authority.bundle", b"bundle"),
                ("workspace.json", b"{}\n"),
                ("final/manifest.json", b"{}\n"),
            ):
                destination = exp_dir / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                files.append(
                    {
                        "path": name,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            for path in sorted((exp_dir / "run").rglob("*")):
                relative = path.relative_to(exp_dir).as_posix()
                if path.is_file() and relative not in {
                    item["path"] for item in files
                }:
                    data = path.read_bytes()
                    files.append(
                        {
                            "path": relative,
                            "size_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
        proof_path.write_text(
            json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        proof_bytes = proof_path.read_bytes()
        files.append(
            {
                "path": "run/provider-free-execution.json",
                "size_bytes": len(proof_bytes),
                "sha256": hashlib.sha256(proof_bytes).hexdigest(),
            }
        )
        manifest = {
            "schema_version": 1,
            "workload_status": 0,
            "final_status": 0,
            "files": files,
        }
        (exp_dir / "artifact_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_provider_free_failure_evidence(
        self,
        handle: str,
        *,
        scenario_identity: str = "issue15.provider-free.runtime-authority/1",
        stage: str = "viewer_fallback",
        operation: str | None = None,
        stripped: list[str] | None = None,
        browser_identity_substage: str | None = None,
        browser_identity_phase: str | None = None,
        browser_identity_check: str | None = None,
    ) -> None:
        """Publish common authority plus one manifest-bound closed failure."""

        self.write_provider_free_terminal_evidence(handle, stripped=stripped)
        exp_dir = self.repo_root / "outputs" / handle
        for relative in (
            "run/runtime-authority-smoke.json",
            "workspace-authority.json",
            "workspace-authority.bundle",
            "workspace.json",
            "final/manifest.json",
        ):
            (exp_dir / relative).unlink()
        failure = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": scenario_identity,
            "stage": stage,
        }
        if operation is not None:
            failure["operation"] = operation
        if browser_identity_substage is not None:
            failure["browser_identity_substage"] = browser_identity_substage
        if browser_identity_phase is not None:
            failure["browser_identity_phase"] = browser_identity_phase
        if browser_identity_check is not None:
            failure["browser_identity_check"] = browser_identity_check
        if operation in protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS:
            (
                exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            ).write_text(
                json.dumps(
                    {
                        "schema": (
                            protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA
                        ),
                        "operation": operation,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
        failure_path = exp_dir / "run/scenario-failure.json"
        failure_path.write_text(
            json.dumps(failure, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if browser_identity_substage is not None:
            browser_exec_path = (
                exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            )
            browser_exec = json.loads(
                browser_exec_path.read_text(encoding="utf-8")
            )
            browser_exec["prelaunched_cdp"] = (
                "passed"
                if browser_identity_substage == "runtime_evidence_cross_binding"
                else "failed"
            )
            browser_exec_path.write_text(
                json.dumps(browser_exec, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            identity_diagnostic = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": browser_identity_substage,
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": hashlib.sha256(
                        failure_path.read_bytes()
                    ).hexdigest(),
                },
            }
            if browser_identity_phase is not None:
                identity_diagnostic["phase"] = browser_identity_phase
            if browser_identity_check is not None:
                identity_diagnostic["check"] = browser_identity_check
            identity_path = (
                exp_dir
                / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
            )
            identity_path.write_text(
                json.dumps(
                    identity_diagnostic,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
        files = []
        for path in sorted(exp_dir.rglob("*")):
            if not path.is_file() or path.name == "artifact_manifest.json":
                continue
            data = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(exp_dir).as_posix(),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        (exp_dir / "artifact_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workload_status": 1,
                    "final_status": 1,
                    "files": files,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_submit_returns_stable_handle_before_child_start(self) -> None:
        handle = self.submit()
        self.assertRegex(
            handle,
            rf"^{self.group}/\d{{8}}-\d{{6}}-airplane$",
        )
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["state"], "submitted")
        self.assertIsNone(state["supervisor_pid"])
        self.assertEqual(self.detached[0], handle)
        self.assertIn("supervise-pilot", self.detached[1])

    def test_provider_free_submit_binds_scenario_and_execution_profile(self) -> None:
        detached: dict[str, object] = {}

        def fake_detach(handle, command, root):
            detached.update(handle=handle, command=list(command), root=root)
            return 1234

        result = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=fake_detach,
        )

        handle = result["job"]
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(result["kind"], "provider-free")
        self.assertEqual(state["job_kind"], "provider-free")
        self.assertEqual(
            state["scenario"],
            {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
        )
        self.assertEqual(
            state["execution_profile"],
            {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/16",
                "provider_access": "forbidden",
                "sandbox_profile": "cvm.provider-free-linux-sandbox/16",
            },
        )
        self.assertEqual(
            state["request_authority"]["deployment_tree_sha256"],
            json.loads(
                (self.repo_root / ".cvm-deployment.json").read_text(encoding="utf-8")
            )["tree_sha256"],
        )
        self.assertEqual(
            state["request_authority_sha256"],
            protocol.request_authority_sha256(state),
        )
        self.assertIn("supervise-provider-free", detached["command"])

    def test_provider_free_submit_cli_returns_compact_stable_handle(self) -> None:
        expected = {
            "job": f"{self.group}/20260811-210000-issue15-runtime-authority",
            "state": "submitted",
            "kind": "provider-free",
        }
        with (
            mock.patch.object(runtime, "submit_provider_free", return_value=expected),
            mock.patch.object(cvm_job_cli, "submit_provider_free", return_value=expected),
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout,
        ):
            status = cvm_job_cli.main(
                [
                    "--state-root",
                    os.fspath(self.state_root),
                    "submit-provider-free",
                    "issue15-runtime-authority",
                    self.group,
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_provider_free_supervisor_cli_reports_terminal_state(self) -> None:
        handle = f"{self.group}/20260811-210000-issue15-runtime-authority"
        expected = {
            "job": handle,
            "state": "succeeded",
            "kind": "pilot",
            "job_kind": "provider-free",
        }
        with (
            mock.patch.object(
                cvm_job_cli,
                "supervise_provider_free",
                return_value=expected,
                create=True,
            ) as supervise,
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout,
        ):
            status = cvm_job_cli.main(
                [
                    "--state-root",
                    os.fspath(self.state_root),
                    "supervise-provider-free",
                    "--job",
                    handle,
                ]
            )

        self.assertEqual(status, 0)
        supervise.assert_called_once_with(handle, state_root=self.state_root)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_provider_free_supervisor_uses_closed_runner_and_stripped_environment(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        captured: dict[str, object] = {}

        def fake_run(root, job, command, **kwargs):
            captured["command"] = list(command)
            captured["env"] = dict(kwargs["env"])
            self.write_provider_free_terminal_evidence(
                handle,
                stripped=kwargs["env"][
                    "CVM_PROVIDER_FREE_STRIPPED_NAMES"
                ].split(","),
            )
            return 0, 4321

        hostile_environment = {
            "PATH": os.environ["PATH"],
            "HOME": os.fspath(self.workspace),
            "LANG": "host-controlled-locale",
            "LC_ALL": "host-controlled-locale",
            "LC_CTYPE": "host-controlled-lookalike",
            "__CF_USER_TEXT_ENCODING": "host-controlled-lookalike",
            "VENUS_TOKEN": "do-not-forward",
            "OPENAI_API_KEY": "do-not-forward",
            "ANTHROPIC_API_KEY": "do-not-forward",
            "HTTPS_PROXY": "http://provider-proxy.invalid",
            "PYTHONPATH": "/host-controlled/python",
        }
        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ=hostile_environment,
            )

        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(
            captured["command"],
            [
                sys.executable,
                "-m",
                runtime.PROVIDER_FREE_RUNNER_MODULE,
                "run",
                "issue15-runtime-authority",
                self.group,
                protocol.parse_handle(handle)["exp"],
            ],
        )
        child_environment = captured["env"]
        self.assertEqual(
            child_environment["CVM_PROVIDER_FREE_PROFILE"],
            "issue15.provider-free-bounded/16",
        )
        self.assertEqual(
            child_environment["CVM_PROVIDER_FREE_STRIPPED_NAMES"],
            (
                "ANTHROPIC_API_KEY,HTTPS_PROXY,LC_ALL,LC_CTYPE,OPENAI_API_KEY,"
                "PYTHONPATH,VENUS_TOKEN,__CF_USER_TEXT_ENCODING"
            ),
        )
        self.assertEqual(child_environment["LANG"], "C.UTF-8")
        self.assertEqual(child_environment["LC_ALL"], "C.UTF-8")
        for forbidden in (
            "VENUS_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HTTPS_PROXY",
            "PYTHONPATH",
            "LC_CTYPE",
            "__CF_USER_TEXT_ENCODING",
        ):
            self.assertNotIn(forbidden, child_environment)
        self.assertEqual(
            state["no_provider_evidence"],
            f"outputs/{handle}/run/provider-free-execution.json",
        )
        with mock.patch.object(runtime, "_observe_pilot", return_value={}):
            public = runtime.status_job(handle, state_root=self.state_root)
        self.assertEqual(public["kind"], "provider-free")
        self.assertEqual(public["scenario"], state["scenario"])
        self.assertNotIn("bootstrap_diagnostic", public)

    def test_provider_free_supervisor_clean_environment_crosses_python_startup(self) -> None:
        shutil.copytree(
            REPO_ROOT / "scripts",
            self.repo_root / "scripts",
            dirs_exist_ok=True,
        )
        previous_receipt = json.loads(
            (self.repo_root / deployment_authority.RECEIPT_PATH).read_text(
                encoding="utf-8"
            )
        )
        deployment_authority.write_receipt(
            self.repo_root,
            source_head=previous_receipt["source_head"],
            runtime_identity=previous_receipt["runtime_identity"],
        )
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        caller_cwd = self.workspace / "non-repository-caller"
        caller_cwd.mkdir()
        supervisor_log = protocol.log_path(self.state_root, handle)
        supervisor_log.parent.mkdir(parents=True, exist_ok=True)
        original_cwd = Path.cwd()
        try:
            os.chdir(caller_cwd)
            with supervisor_log.open("ab", buffering=0) as stream:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.pilot.cvm_job",
                        "--state-root",
                        os.fspath(self.state_root),
                        "supervise-provider-free",
                        "--job",
                        handle,
                    ],
                    cwd=self.repo_root,
                    env={
                        "HOME": os.fspath(self.workspace),
                        "PATH": os.environ["PATH"],
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(completed.returncode, 1)
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["state"], "failed")
        diagnostic = state["bootstrap_diagnostic"]
        self.assertEqual(
            {
                key: diagnostic[key]
                for key in ("schema", "phase", "process_exit_code")
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "process_exit_code": 2,
            },
        )
        self.assertIn(
            diagnostic["classification"],
            {"runner-bwrap-path-rejected", "runner-runtime-identity-rejected"},
        )
        self.assertNotIn(
            "ModuleNotFoundError",
            supervisor_log.read_text(encoding="utf-8"),
        )

    def test_provider_free_supervisor_rejects_incomplete_terminal_evidence(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, complete=False)
            return 0, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "HOME": os.fspath(self.workspace),
                    "VENUS_TOKEN": "do-not-forward",
                    "OPENAI_API_KEY": "do-not-forward",
                    "ANTHROPIC_API_KEY": "do-not-forward",
                    "HTTPS_PROXY": "http://provider-proxy.invalid",
                },
            )

        self.assertEqual(state["state"], "failed")
        self.assertIn("terminal evidence", state["failure_reason"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertNotIn("bootstrap_diagnostic", public)

    def test_provider_free_nonzero_exposes_closed_scenario_failure(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="voxblame_preview",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"], "OPENAI_API_KEY": "secret"},
            )

        expected = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "native_measurement",
            "operation": "voxblame_preview",
        }
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["scenario_failure"], expected)
        self.assertNotIn("missing", state["failure_reason"])
        self.assertNotIn("invalid retained", state["failure_reason"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        waited, exit_code = runtime.wait_job(
            handle,
            state_root=self.state_root,
            timeout=0,
        )
        for result in (public, waited):
            self.assertEqual(result["scenario_failure"], expected)
            self.assertEqual(result["process_exit_code"], 1)
            self.assertEqual(result["runner_final_status"], 1)
        self.assertEqual(exit_code, 1)

    def test_monitor_projects_manifest_bound_browser_identity_substage(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage="live_running_image_identity",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        expected = {
            "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "substage": "live_running_image_identity",
        }
        self.assertEqual(expected, state["browser_identity_diagnostic"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(expected, public["browser_identity_diagnostic"])
        self.assertNotIn("sha256", json.dumps(public))

    def test_monitor_projects_manifest_bound_private_snapshot_phase(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage=(
                    "private_snapshot_launch_image_identity"
                ),
                browser_identity_phase="private_tree_materialization",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        expected = {
            "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "substage": "private_snapshot_launch_image_identity",
            "phase": "private_tree_materialization",
        }
        self.assertEqual(expected, state["browser_identity_diagnostic"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(expected, public["browser_identity_diagnostic"])
        self.assertNotIn("sha256", json.dumps(public))

    def test_monitor_projects_manifest_bound_package_revision_check(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage="private_snapshot_launch_image_identity",
                browser_identity_phase="playwright_package_revision_identity",
                browser_identity_check="frozen_playwright_version_match",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        expected = {
            "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "substage": "private_snapshot_launch_image_identity",
            "phase": "playwright_package_revision_identity",
            "check": "frozen_playwright_version_match",
        }
        self.assertEqual(expected, state["browser_identity_diagnostic"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(expected, public["browser_identity_diagnostic"])
        self.assertNotIn("sha256", json.dumps(public))

    def test_monitor_projects_manifest_bound_version_execution_check(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage=(
                    "private_snapshot_launch_image_identity"
                ),
                browser_identity_phase="private_launch_version_execution",
                browser_identity_check="private_version_probe_timeout",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        expected = {
            "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "substage": "private_snapshot_launch_image_identity",
            "phase": "private_launch_version_execution",
            "check": "private_version_probe_timeout",
        }
        self.assertEqual(expected, state["browser_identity_diagnostic"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(expected, public["browser_identity_diagnostic"])
        self.assertNotIn("sha256", json.dumps(public))

    def test_supervisor_rejects_recomputed_duplicate_private_phase(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage=(
                    "private_snapshot_launch_image_identity"
                ),
                browser_identity_phase="private_launch_image_identity",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            exp_dir = self.repo_root / "outputs" / handle
            failure_path = exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH
            failure_path.write_text(
                "{\"schema\":\"cvm.provider-free-scenario-failure/1\","
                "\"scenario_identity\":\"issue15.provider-free.runtime-authority/1\","
                "\"stage\":\"native_measurement\","
                "\"operation\":\"preview_browser_identity\","
                "\"browser_identity_substage\":\"private_snapshot_launch_image_identity\","
                "\"browser_identity_phase\":\"source_executable_identity\","
                "\"browser_identity_phase\":\"private_launch_image_identity\"}",
                encoding="utf-8",
            )
            diagnostic_path = (
                exp_dir / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
            )
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            diagnostic["scenario_failure"]["sha256"] = hashlib.sha256(
                failure_path.read_bytes()
            ).hexdigest()
            diagnostic_path.write_text(
                json.dumps(diagnostic, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for relative in (
                protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
            ):
                data = (exp_dir / relative).read_bytes()
                entry = next(
                    item
                    for item in manifest["files"]
                    if item["path"] == relative
                )
                entry.update(
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertNotIn("scenario_failure", state)
        self.assertNotIn("browser_identity_diagnostic", state)
        self.assertIn(
            "scenario failure evidence is invalid",
            state["failure_reason"],
        )

    def test_supervisor_rejects_recomputed_duplicate_package_check(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage="private_snapshot_launch_image_identity",
                browser_identity_phase="playwright_package_revision_identity",
                browser_identity_check="browser_manifest_entry",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            exp_dir = self.repo_root / "outputs" / handle
            diagnostic_path = (
                exp_dir / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
            )
            diagnostic = diagnostic_path.read_text(encoding="utf-8")
            diagnostic_path.write_text(
                diagnostic.replace(
                    '"check":"browser_manifest_entry"',
                    '"check":"python_distribution_metadata",'
                    '"check":"browser_manifest_entry"',
                ),
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = diagnostic_path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"]
                == protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
            )
            entry.update(
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertNotIn("scenario_failure", state)
        self.assertNotIn("browser_identity_diagnostic", state)
        self.assertIn(
            "browser identity diagnostic evidence is invalid",
            state["failure_reason"],
        )

    def test_supervisor_rejects_duplicate_manifest_bound_public_wrapper(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_identity",
                browser_identity_substage="live_running_image_identity",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            exp_dir = self.repo_root / "outputs" / handle
            wrapper_path = (
                exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            )
            wrapper_path.write_text(
                "{\"schema\":\"cvm.provider-free-preview-public-wrapper/1\","
                "\"operation\":\"passed\","
                "\"operation\":\"preview_browser_identity\"}",
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            wrapper_bytes = wrapper_path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"]
                == protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            )
            entry.update(
                size_bytes=len(wrapper_bytes),
                sha256=hashlib.sha256(wrapper_bytes).hexdigest(),
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 1, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertNotIn("scenario_failure", state)
        self.assertNotIn("browser_identity_diagnostic", state)
        self.assertIn(
            "preview public wrapper evidence is invalid",
            state["failure_reason"],
        )

    def test_monitor_rejects_unbound_browser_exec_diagnostic(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation="preview_browser_outer_exec_probe",
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertNotIn("scenario_failure", state)
        self.assertIn("browser exec diagnostic", state["failure_reason"])

    def test_real_runner_startup_projects_manifest_bound_scenario_failure(
        self,
    ) -> None:
        """Exercise the isolated module launch through the public supervisor seam."""

        for relative in (
            "scripts/pilot/deployment_authority.py",
            "scripts/pilot/provider_free_scenarios.py",
            "scripts/pilot/cvm_job/__init__.py",
            "scripts/pilot/cvm_job/protocol.py",
        ):
            destination = self.repo_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, destination)
        deployed = json.loads(
            (self.repo_root / deployment_authority.RECEIPT_PATH).read_bytes()
        )
        deployment_authority.write_receipt(
            self.repo_root,
            source_head=deployed["source_head"],
            runtime_identity=deployed["runtime_identity"],
        )
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        real_run = subprocess.run

        def execute_sandbox(argv, **kwargs):
            if "--" not in argv:
                return real_run(argv, **kwargs)
            command = list(argv)[list(argv).index("--") + 1 :]
            command = [
                value.replace("/workspace/repo", os.fspath(self.repo_root), 1)
                if value.startswith("/workspace/repo")
                else value
                for value in command
            ]
            command[0] = sys.executable
            emulated_environment = dict(kwargs["env"])
            emulated_environment.pop("MESHSHOT_EXECUTABLE_ROOT", None)
            return real_run(
                command,
                cwd=self.repo_root,
                env=emulated_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        def run_real_runner(*_args, **kwargs):
            state = protocol.load_state(self.state_root, handle)
            with (
                mock.patch.object(
                    provider_free_runner,
                    "REPO_ROOT",
                    self.repo_root,
                ),
                mock.patch.object(
                    provider_free_runner,
                    "_trusted_runtime",
                    return_value=state["request_authority"]["runtime_identity"],
                ),
                mock.patch.object(
                    provider_free_runner.subprocess,
                    "run",
                    side_effect=execute_sandbox,
                ),
            ):
                status = provider_free_runner.main(
                    [
                        "run",
                        "issue15-runtime-authority",
                        state["group"],
                        state["exp"],
                    ],
                    environ=kwargs["env"],
                )
            return status, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=run_real_runner,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        expected = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "viewer_deployment",
        }
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["process_exit_code"], 1)
        self.assertEqual(state["runner_final_status"], 1)
        self.assertEqual(state["scenario_failure"], expected)
        manifest = json.loads(
            (
                self.repo_root / "outputs" / handle / "artifact_manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn(
            "run/scenario-failure.json",
            {entry["path"] for entry in manifest["files"]},
        )

    def _install_real_provider_free_layout(
        self,
        *,
        noisy_source: bool = False,
        abrupt_source: bool = False,
        measurement_failure: bool = False,
    ) -> Path:
        """Install the production deployment layout used by the real-chain tests."""
        for relative in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            if relative == "skills/cad-viewer/scripts/viewer":
                continue
            source = REPO_ROOT / relative
            destination = self.repo_root / relative
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination, symlinks=False)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        preview_profile = provider_free_scenarios.PREVIEW_PROFILE.relative_to(
            REPO_ROOT
        )
        preview_destination = self.repo_root / preview_profile
        preview_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / preview_profile, preview_destination)
        native_runtime = self.repo_root / (
            "skills/mesh-compare/scripts/packages/meshscope"
        )
        native_build = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd=native_runtime,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, native_build.returncode, native_build.stderr)
        launcher = """\
import http from "node:http";

const args = process.argv.slice(2);
const value = (name) => args[args.indexOf(name) + 1];
const host = value("--host");
const directory = value("--dir");
const requestedPort = Number(value("--port"));
await fetch(`http://${host}:${requestedPort}/__cad/directory/activate`, {
  method: "POST",
  headers: {"content-type": "application/json"},
  body: JSON.stringify({directory}),
});
const server = http.createServer((request, response) => {
  const url = new URL(request.url, `http://${host}`);
  if (url.pathname !== "/__cad/server") {
    response.writeHead(404).end();
    return;
  }
  const payload = JSON.stringify({rootPath: directory, pid: process.pid});
  response.writeHead(200, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(payload),
  });
  response.end(payload);
});
server.listen(0, host, () => {
  const port = server.address().port;
  const url = `http://${host}:${port}?dir=${encodeURIComponent(directory)}`;
  process.stdout.write(`${JSON.stringify({action: "start", port, url})}\n`);
});
"""
        viewer_files = {
            "viewer/scripts/start-agent-viewer.mjs": launcher,
            "viewer/src/server/server.mjs": "export const server = true;\n",
            "viewer/src/client/main.jsx": "export const client = true;\n",
            "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs": launcher,
            "skills/cad-viewer/scripts/viewer/backend/server.mjs": (
                "export const server = true;\n"
            ),
            "skills/cad-viewer/scripts/viewer/dist/index.html": (
                "<!doctype html><title>viewer</title>\n"
            ),
        }
        for relative, content in viewer_files.items():
            destination = self.repo_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        artifacts = []
        for role, source, bundle in (
            (
                "launcher",
                "viewer/scripts/start-agent-viewer.mjs",
                "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
            ),
            (
                "server",
                "viewer/src/server/server.mjs",
                "skills/cad-viewer/scripts/viewer/backend/server.mjs",
            ),
            (
                "client",
                "viewer/src/client/main.jsx",
                "skills/cad-viewer/scripts/viewer/dist/index.html",
            ),
        ):
            artifacts.append(
                {
                    "role": role,
                    "source": {
                        "path": source,
                        "sha256": hashlib.sha256(
                            (self.repo_root / source).read_bytes()
                        ).hexdigest(),
                    },
                    "bundle": {
                        "path": bundle,
                        "sha256": hashlib.sha256(
                            (self.repo_root / bundle).read_bytes()
                        ).hexdigest(),
                    },
                }
            )
        identity = self.repo_root / (
            "skills/cad-viewer/scripts/viewer/runtime-identity.json"
        )
        identity.write_text(
            json.dumps(
                {
                    "schema": "cad-viewer.runtime-identity/1",
                    "viewer_version": "0.3.9",
                    "artifacts": artifacts,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        provider_free_scenarios.deployed_viewer_receipt(self.repo_root)
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required for the production Viewer fallback seam")
        git_lfs = shutil.which("git-lfs")
        if git_lfs is None:
            self.skipTest("git-lfs is required for the production Workspace seam")
        fake_bin = self.repo_root / ".venv/bin"
        fake_bin.mkdir(parents=True)
        (fake_bin / "node").symlink_to(Path(node).resolve(strict=True))
        (fake_bin / "git-lfs").symlink_to(Path(git_lfs).resolve(strict=True))
        provider_home = self.workspace / "provider-free-home"
        provider_home.mkdir()

        if noisy_source or abrupt_source:
            durable_source = self.repo_root / (
                provider_free_scenarios.DURABLE_MODEL_SOURCE.relative_to(
                    REPO_ROOT
                )
            )
            prefix = (
                "import os\nos._exit(23)\n"
                if abrupt_source
                else "print('candidate noise')\n"
            )
            durable_source.write_text(
                prefix + durable_source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        if measurement_failure:
            measurement_cli = self.repo_root / (
                "skills/mesh-compare/scripts/mesh-compare/cli.py"
            )
            marker = "def _measure_main(argv: list[str]) -> int:\n"
            source = measurement_cli.read_text(encoding="utf-8")
            self.assertEqual(1, source.count(marker))
            measurement_cli.write_text(
                source.replace(
                    marker,
                    marker
                    + "    raise PermissionError("
                    + repr("OPENAI_API_KEY=secret native measurement failure")
                    + ")\n",
                ),
                encoding="utf-8",
            )

        deployed = json.loads(
            (self.repo_root / deployment_authority.RECEIPT_PATH).read_bytes()
        )
        runtime_identity = deployed["runtime_identity"]
        cadpy = self.repo_root / deployment_authority.CADPY_RUNTIME_PATH
        runtime_identity["cadpy"]["sha256"] = hashlib.sha256(
            cadpy.read_bytes()
        ).hexdigest()
        deployment_authority.write_receipt(
            self.repo_root,
            source_head=deployed["source_head"],
            runtime_identity=runtime_identity,
        )
        return provider_home

    def _run_provider_free_chain_with_bwrap_emulator(
        self,
        provider_home: Path,
    ) -> tuple[dict[str, object], str, dict[str, object]]:
        """Run real public layers with only the outer bwrap syscall emulated."""
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        real_run = subprocess.run
        captured: dict[str, object] = {}

        def execute_sandbox(argv, **kwargs):
            if "--" not in argv:
                return real_run(argv, **kwargs)
            stable_revision = (
                f"{protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested"
            )
            browser_binds = [
                (Path(argv[index + 1]), argv[index + 2])
                for index, value in enumerate(argv[:-2])
                if value == "--ro-bind"
                and argv[index + 2] == stable_revision
            ]
            self.assertEqual(1, len(browser_binds), list(argv))
            host_revision, sandbox_revision = browser_binds[0]
            self.assertEqual(stable_revision, sandbox_revision)
            self.assertTrue(host_revision.is_dir())
            host_stage = host_revision.parent
            executable_relative = Path(
                protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
            ).relative_to(protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE)
            host_executable = host_stage / executable_relative
            self.assertTrue(host_executable.is_file())
            captured["browser_stage_projection"] = {
                "source": os.fspath(host_revision),
                "destination": sandbox_revision,
            }
            command = list(argv)[list(argv).index("--") + 1 :]
            command = [
                value.replace("/workspace/repo", os.fspath(self.repo_root), 1)
                if value.startswith("/workspace/repo")
                else value
                for value in command
            ]
            command[0] = sys.executable
            sandbox_environment = dict(kwargs["env"])
            captured["sandbox_argv"] = list(argv)
            captured["sandbox_environment"] = dict(sandbox_environment)
            emulated_environment = {
                name: value.replace(
                    "/workspace/repo", os.fspath(self.repo_root)
                ).replace(
                    "/home/provider-free", os.fspath(provider_home)
                ).replace(
                    protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE,
                    os.fspath(host_stage),
                )
                for name, value in sandbox_environment.items()
            }
            emulated_environment["MESHSHOT_BROWSER_EXECUTABLE"] = os.fspath(
                host_executable
            )
            emulated_environment.pop("MESHSHOT_EXECUTABLE_ROOT", None)
            captured["emulated_environment"] = emulated_environment
            completed = real_run(
                command,
                cwd=self.repo_root,
                env=emulated_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            captured["scenario_stderr"] = completed.stderr
            return completed

        def run_real_runner(*_args, **kwargs):
            state = protocol.load_state(self.state_root, handle)
            with (
                mock.patch.object(
                    provider_free_runner,
                    "REPO_ROOT",
                    self.repo_root,
                ),
                mock.patch.object(
                    provider_free_runner,
                    "_trusted_runtime",
                    return_value=state["request_authority"]["runtime_identity"],
                ),
                mock.patch.object(
                    provider_free_runner.subprocess,
                    "run",
                    side_effect=execute_sandbox,
                ),
            ):
                status = provider_free_runner.main(
                    [
                        "run",
                        "issue15-runtime-authority",
                        state["group"],
                        state["exp"],
                    ],
                    environ=kwargs["env"],
                )
            return status, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=run_real_runner,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "HOME": os.fspath(self.workspace),
                    "OPENAI_API_KEY": "must-not-cross-runner",
                    "PYTHONPATH": "/must/not/cross/runner",
                },
            )
        return state, handle, captured

    def _canonical_build_argv(self) -> list[str]:
        return [
            sys.executable,
            os.fspath(self.repo_root / "skills/cad/scripts/canonical-build"),
            "build",
            "--source",
            "source/model.py",
            "--input",
            "source/simple_model_library.py",
            "--output-dir",
            "built",
            "--reject-source-output",
        ]

    def test_measurement_public_seam_owns_bundled_packages_without_pythonpath(
        self,
    ) -> None:
        """The deployed CLI must not resolve an older editable meshscope first."""

        self._install_real_provider_free_layout()
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "from scripts.pilot import provider_free_scenarios as scenario\n"
                    "from scripts.pilot import runner\n"
                    "workspace = Path('outputs/public-measurement-seam')\n"
                    "runner.prepare_exp(workspace)\n"
                    "commands = workspace / 'run/provider-free-commands.jsonl'\n"
                    "candidate = scenario._prepare_candidate(workspace, commands)\n"
                    "scenario._prepare_workspace(workspace, candidate, commands)\n"
                    "measured = scenario._run_public([\n"
                    "    sys.executable, str(scenario.MESH_COMPARE),\n"
                    "    'voxblame-measure', str(candidate / 'built/measurement.glb'),\n"
                    "    '--reference', str(workspace / 'input'),\n"
                    "    '--output', str(workspace / 'voxblame'),\n"
                    "    '--step', '0',\n"
                    "], cwd=scenario.REPO_ROOT, command_log=commands)\n"
                    "scenario.native_depth_eight_evidence(measured)\n"
                ),
            ],
            cwd=self.repo_root,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_bwrap_emulator_crosses_canonical_candidate_measurement_with_production_layout(
        self,
    ) -> None:
        """Drive public layers while explicitly emulating the bwrap syscall."""

        provider_home = self._install_real_provider_free_layout()
        state, handle, captured = self._run_provider_free_chain_with_bwrap_emulator(
            provider_home
        )

        exp_dir = self.repo_root / "outputs" / handle
        candidate = exp_dir / "work/candidate"
        viewer_stderr_path = exp_dir / "run/viewer-fallback.stderr.log"
        self.assertTrue(
            (candidate / "built/measurement.glb").is_file(),
            json.dumps(
                {
                    "state": state,
                    "scenario_stderr": captured.get("scenario_stderr"),
                    "viewer_stderr": (
                        viewer_stderr_path.read_text(encoding="utf-8")
                        if viewer_stderr_path.is_file()
                        else None
                    ),
                },
                sort_keys=True,
            ),
        )
        command_log_path = exp_dir / "run/provider-free-commands.jsonl"
        self.assertTrue(
            (exp_dir / "input/input.json").is_file(),
            json.dumps(
                {
                    "state": state,
                    "scenario_stderr": captured.get("scenario_stderr"),
                    "commands": (
                        command_log_path.read_text(encoding="utf-8")
                        if command_log_path.is_file()
                        else None
                    ),
                },
                sort_keys=True,
            ),
        )
        self.assertTrue((exp_dir / "workspace.json").is_file())
        normalization = json.loads(
            (exp_dir / "input/normalization.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            normalization["raw_to_canonical"],
            json.dumps(
                {
                    "state": state,
                    "scenario_stderr": captured.get("scenario_stderr"),
                    "normalization": normalization,
                },
                sort_keys=True,
            ),
        )
        if state["state"] == "failed":
            self.assertIn(
                state["scenario_failure"]["stage"],
                {"native_measurement", "finalization"},
            )
        else:
            self.assertEqual(state["state"], "succeeded")

        command_records = [
            json.loads(line)
            for line in command_log_path
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        canonical_argv = self._canonical_build_argv()
        self.assertEqual(command_records[0]["argv"], canonical_argv)
        self.assertEqual(command_records[0]["cwd"], os.fspath(candidate))
        self.assertEqual(command_records[0]["exit_code"], 0)
        self.assertIn("voxblame-prepare-reference", command_records[1]["argv"])
        self.assertIn("init", command_records[2]["argv"])
        measurement_record = next(
            record
            for record in command_records
            if "voxblame-measure" in record["argv"]
        )
        self.assertEqual(
            0,
            measurement_record["exit_code"],
            json.dumps(measurement_record, sort_keys=True),
        )
        self.assertEqual(
            captured["sandbox_environment"],
            {
                **runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT,
                "LANG": "C.UTF-8",
            },
        )
        self.assertNotIn("PYTHONPATH", captured["emulated_environment"])
        self.assertIn("--unshare-net", captured["sandbox_argv"])
        for flag in (
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
        ):
            self.assertIn(flag, captured["sandbox_argv"])
        self.assertEqual(
            "ALL",
            captured["sandbox_argv"][
                captured["sandbox_argv"].index("--cap-drop") + 1
            ],
        )
        self.assertEqual(
            state["request_authority"]["runtime_identity"]["bwrap"]["path"],
            captured["sandbox_argv"][0],
        )
        self.assertNotIn("OPENAI_API_KEY", captured["sandbox_environment"])
        self.assertNotIn("PYTHONPATH", captured["sandbox_environment"])

    def test_bwrap_emulator_rejects_noisy_candidate_without_publication(
        self,
    ) -> None:
        """Reject candidate stdout before publishing formal build artifacts."""

        provider_home = self._install_real_provider_free_layout(
            noisy_source=True
        )
        noisy_state, handle, captured = self._run_provider_free_chain_with_bwrap_emulator(
            provider_home
        )

        noisy_exp_dir = self.repo_root / "outputs" / handle
        noisy_candidate = noisy_exp_dir / "work/candidate"
        self.assertEqual(noisy_state["state"], "failed")
        self.assertEqual(
            noisy_state["scenario_failure"]["stage"],
            "candidate_workspace",
        )
        self.assertFalse((noisy_candidate / "built/measurement.glb").exists())
        self.assertFalse((noisy_candidate / "built/build.json").exists())
        noisy_records = [
            json.loads(line)
            for line in (
                noisy_exp_dir / "run/provider-free-commands.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(noisy_records), 1)
        self.assertEqual(noisy_records[0]["argv"], self._canonical_build_argv())
        self.assertNotEqual(noisy_records[0]["exit_code"], 0)
        self.assertNotIn(
            "candidate noise",
            str(captured.get("scenario_stderr", "")),
        )
        self.assertEqual(
            noisy_records[0]["stdout_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(
            captured["sandbox_environment"],
            {
                **runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT,
                "LANG": "C.UTF-8",
            },
        )

    def test_real_chain_projects_manifest_bound_native_measurement_operation(
        self,
    ) -> None:
        """Bind a native public-command failure through scenario to supervisor."""

        provider_home = self._install_real_provider_free_layout(
            measurement_failure=True
        )
        state, handle, captured = self._run_provider_free_chain_with_bwrap_emulator(
            provider_home
        )

        expected = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "native_measurement",
            "operation": "voxblame_measure",
        }
        self.assertEqual("failed", state["state"])
        self.assertEqual(1, state["process_exit_code"])
        self.assertEqual(1, state["runner_final_status"])
        self.assertEqual(expected, state["scenario_failure"])
        exp_dir = self.repo_root / "outputs" / handle
        receipt_text = (exp_dir / "run/scenario-failure.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(expected, json.loads(receipt_text))
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "run/scenario-failure.json",
            {entry["path"] for entry in manifest["files"]},
        )
        command_records = [
            json.loads(line)
            for line in (
                exp_dir / "run/provider-free-commands.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        measurement = next(
            record
            for record in command_records
            if "voxblame-measure" in record["argv"]
        )
        self.assertNotEqual(0, measurement["exit_code"])
        for public_value in (
            state,
            receipt_text,
            captured.get("scenario_stderr", ""),
        ):
            self.assertNotIn("OPENAI_API_KEY", str(public_value))
            self.assertNotIn("native measurement failure", str(public_value))

    def test_bwrap_emulator_proves_nested_worker_survives_abrupt_source(
        self,
    ) -> None:
        """An os._exit source kills only the nested worker, not public layers."""

        provider_home = self._install_real_provider_free_layout(
            abrupt_source=True
        )
        state, handle, captured = (
            self._run_provider_free_chain_with_bwrap_emulator(provider_home)
        )

        exp_dir = self.repo_root / "outputs" / handle
        candidate = exp_dir / "work/candidate"
        self.assertEqual("failed", state["state"])
        self.assertEqual(
            "candidate_workspace",
            state["scenario_failure"]["stage"],
        )
        self.assertFalse((candidate / "built/build.json").exists())
        command_records = [
            json.loads(line)
            for line in (
                exp_dir / "run/provider-free-commands.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(1, len(command_records))
        self.assertEqual(self._canonical_build_argv(), command_records[0]["argv"])
        self.assertNotEqual(0, command_records[0]["exit_code"])
        self.assertEqual(
            state["request_authority"]["runtime_identity"]["bwrap"]["path"],
            captured["sandbox_argv"][0],
        )
        self.assertIn("--unshare-net", captured["sandbox_argv"])
        self.assertNotIn("TOP SECRET", str(captured.get("scenario_stderr", "")))

    def test_provider_free_failure_rejects_unknown_terminal_manifest_schema(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            manifest_path = (
                self.repo_root / "outputs" / handle / "artifact_manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 2
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 1, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual(state["state"], "failed")
        self.assertNotIn("scenario_failure", state)
        self.assertIn("schema", state["failure_reason"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertNotIn("scenario_failure", public)

    def test_monitor_projects_manifest_bound_preview_public_wrapper_failure(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        operation = "preview_public_unclassified_exit"

        def fake_run(*_args, **kwargs):
            raw_stripped = kwargs["env"]["CVM_PROVIDER_FREE_STRIPPED_NAMES"]
            self.write_provider_free_failure_evidence(
                handle,
                stage="native_measurement",
                operation=operation,
                stripped=raw_stripped.split(",") if raw_stripped else [],
            )
            return 1, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertEqual(operation, state["scenario_failure"]["operation"])

    def test_monitor_projects_wrapper_publication_root_without_wrapper(self) -> None:
        operation = "preview_public_wrapper_evidence_publication"
        for index, extra_wrapper in enumerate((False, True)):
            with self.subTest(extra_wrapper=extra_wrapper):
                group = f"20260813-12{index:04d}-wrapper-root"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]

                def fake_run(*_args, **kwargs):
                    raw_stripped = kwargs["env"][
                        "CVM_PROVIDER_FREE_STRIPPED_NAMES"
                    ]
                    self.write_provider_free_failure_evidence(
                        handle,
                        stage="native_measurement",
                        operation=operation,
                        stripped=(
                            raw_stripped.split(",") if raw_stripped else []
                        ),
                    )
                    exp_dir = self.repo_root / "outputs" / handle
                    wrapper_path = (
                        exp_dir
                        / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
                    )
                    manifest_path = exp_dir / "artifact_manifest.json"
                    if extra_wrapper:
                        wrapper_path.write_text(
                            '{"schema":"partial"', encoding="utf-8"
                        )
                        wrapper_data = wrapper_path.read_bytes()
                        manifest = json.loads(
                            manifest_path.read_text(encoding="utf-8")
                        )
                        wrapper_entry = next(
                            entry
                            for entry in manifest["files"]
                            if entry["path"]
                            == protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
                        )
                        wrapper_entry.update(
                            size_bytes=len(wrapper_data),
                            sha256=hashlib.sha256(wrapper_data).hexdigest(),
                        )
                        manifest_path.write_text(
                            json.dumps(manifest, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                    else:
                        wrapper_path.unlink()
                        manifest = json.loads(
                            manifest_path.read_text(encoding="utf-8")
                        )
                        manifest["files"] = [
                            entry
                            for entry in manifest["files"]
                            if entry["path"]
                            != protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
                        ]
                        manifest_path.write_text(
                            json.dumps(manifest, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                    return 1, 4321

                with mock.patch.object(
                    runtime,
                    "_run_with_heartbeat",
                    side_effect=fake_run,
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={"PATH": os.environ["PATH"]},
                    )

                self.assertEqual("failed", state["state"])
                if extra_wrapper:
                    self.assertNotIn("scenario_failure", state)
                    self.assertIn(
                        "preview public wrapper must be absent",
                        state["failure_reason"],
                    )
                else:
                    self.assertEqual(
                        operation,
                        state["scenario_failure"]["operation"],
                    )

    def test_provider_free_scenario_failure_rejects_unbound_or_open_values(self) -> None:
        for index, mutation in enumerate(
            (
                "tamper",
                "wrong-identity",
                "unknown-operation",
                "operation-on-wrong-stage",
            )
        ):
            with self.subTest(mutation=mutation):
                group = f"20260805-20{index:04d}-audit"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]

                def fake_run(*_args, **kwargs):
                    raw_stripped = kwargs["env"][
                        "CVM_PROVIDER_FREE_STRIPPED_NAMES"
                    ]
                    self.write_provider_free_failure_evidence(
                        handle,
                        scenario_identity=(
                            "issue15.provider-free.other/1"
                            if mutation == "wrong-identity"
                            else "issue15.provider-free.runtime-authority/1"
                        ),
                        stage=(
                            "viewer_fallback"
                            if mutation == "operation-on-wrong-stage"
                            else "candidate_workspace"
                        ),
                        operation=(
                            "shell"
                            if mutation == "unknown-operation"
                            else (
                                "canonical_build"
                                if mutation == "operation-on-wrong-stage"
                                else None
                            )
                        ),
                        stripped=raw_stripped.split(",") if raw_stripped else [],
                    )
                    if mutation == "tamper":
                        failure_path = (
                            self.repo_root
                            / "outputs"
                            / handle
                            / "run/scenario-failure.json"
                        )
                        failure_path.write_text(
                            json.dumps(
                                {
                                    "schema": "cvm.provider-free-scenario-failure/1",
                                    "scenario_identity": (
                                        "issue15.provider-free.runtime-authority/1"
                                    ),
                                    "stage": "native_measurement",
                                },
                                sort_keys=True,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                    return 1, 4321

                with mock.patch.object(
                    runtime,
                    "_run_with_heartbeat",
                    side_effect=fake_run,
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={"PATH": os.environ["PATH"]},
                    )

                self.assertEqual(state["state"], "failed")
                self.assertNotIn("scenario_failure", state)
                self.assertIn("scenario failure", state["failure_reason"])
                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )
                self.assertNotIn("scenario_failure", public)

    def test_public_scenario_failure_projection_strips_unbounded_fields(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        protocol.transition(self.state_root, handle, "running")
        protocol.transition(
            self.state_root,
            handle,
            "failed",
            process_exit_code=1,
            runner_final_status=1,
            scenario_failure={
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "candidate_workspace",
                "operation": "canonical_build",
                "text": "OPENAI_API_KEY=secret\n../../private/path",
                "argv": ["/bin/sh", "-c", "secret"],
                "environment": {"VENUS_TOKEN": "secret"},
                "digest": "d" * 64,
            },
        )

        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )

        self.assertEqual(
            public["scenario_failure"],
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "candidate_workspace",
                "operation": "canonical_build",
            },
        )
        serialized = json.dumps(public, sort_keys=True)
        for forbidden in ("secret", "private/path", "d" * 64, "argv", "environment"):
            self.assertNotIn(forbidden, serialized)

    def test_provider_free_bootstrap_failure_has_bounded_public_diagnostic(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        secret = "credential-value-that-must-not-be-published"
        diagnostic_log = protocol.log_path(self.state_root, handle)
        diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_log.write_text(
            "attacker-controlled-output\n" * 500
            + "Traceback (most recent call last):\n"
            + "ModuleNotFoundError: missing bootstrap module\n"
            + f"OPENAI_API_KEY={secret}\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            return_value=(1, 4321),
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "OPENAI_API_KEY": secret,
                },
            )

        expected = {
            "schema": "cvm.provider-free-bootstrap-diagnostic/1",
            "phase": "before-experiment",
            "classification": "python-import-failed",
            "process_exit_code": 1,
        }
        self.assertEqual(state["state"], "failed")
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        waited, exit_code = runtime.wait_job(
            handle,
            state_root=self.state_root,
            timeout=0,
        )
        self.assertEqual(public["bootstrap_diagnostic"], expected)
        self.assertEqual(waited["bootstrap_diagnostic"], expected)
        self.assertEqual(exit_code, 1)
        serialized = json.dumps(public, sort_keys=True)
        self.assertLess(len(serialized), 1_024)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("attacker-controlled-output", serialized)
        self.assertNotIn("\n", serialized)

    def test_provider_free_runner_contract_failures_have_closed_diagnostics(self) -> None:
        hostile_suffix = (
            " OPENAI_API_KEY=credential-value-that-must-not-be-published\n"
            "../../private/runtime/path\n"
            f"{'d' * 64}\n"
            "ModuleNotFoundError: attacker-controlled suffix\n"
            "provider-free-runner: trusted runtime identity is invalid: injected\n"
            "attacker-controlled-output"
        )
        cases = (
            (
                "provider-free execution profile is missing or stale",
                "runner-execution-profile-rejected",
            ),
            (
                "provider-free environment contains non-allowlisted names:",
                "runner-environment-allowlist-rejected",
            ),
            (
                "provider-free stripped-name receipt is invalid",
                "runner-stripped-name-receipt-rejected",
            ),
            (
                "provider-free CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256 "
                "is missing or invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256 "
                "is missing or invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free immutable request is invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free immutable request digest conflicts",
                "runner-request-digest-rejected",
            ),
            (
                "PATH bwrap does not match trusted system runtime",
                "runner-bwrap-path-rejected",
            ),
            (
                "trusted runtime identity is invalid:",
                "runner-runtime-identity-rejected",
            ),
            (
                "unsafe provider-free output path:",
                "runner-output-path-rejected",
            ),
            (
                "unknown repository-owned failure:",
                "runner-contract-rejected",
            ),
            (
                "unknown repository-owned failure:"
                + "x" * (runtime.PROVIDER_FREE_BOOTSTRAP_LOG_BYTES + 1)
                + "\nprovider-free-runner: trusted runtime identity is invalid: "
                "injected",
                "runner-contract-rejected",
            ),
        )

        for index, (runner_error, classification) in enumerate(cases):
            with self.subTest(classification=classification):
                group = f"20260805-17{index:04d}-audit"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                diagnostic_log = protocol.log_path(self.state_root, handle)
                diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
                diagnostic_log.write_text(
                    "provider-free-runner: " + runner_error + hostile_suffix,
                    encoding="utf-8",
                )

                with mock.patch.object(
                    runtime,
                    "_run_with_heartbeat",
                    return_value=(2, 4321),
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={"PATH": os.environ["PATH"]},
                    )

                expected = {
                    "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                    "phase": "before-experiment",
                    "classification": classification,
                    "process_exit_code": 2,
                }
                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )
                waited, exit_code = runtime.wait_job(
                    handle,
                    state_root=self.state_root,
                    timeout=0,
                )
                self.assertEqual(state["bootstrap_diagnostic"], expected)
                self.assertEqual(public["bootstrap_diagnostic"], expected)
                self.assertEqual(waited["bootstrap_diagnostic"], expected)
                self.assertEqual(exit_code, 1)
                serialized = json.dumps(
                    {"state": state["bootstrap_diagnostic"], "public": public},
                    sort_keys=True,
                )
                for forbidden in (
                    "credential-value-that-must-not-be-published",
                    "OPENAI_API_KEY",
                    "../../private/runtime/path",
                    "d" * 64,
                    "ModuleNotFoundError",
                    "attacker-controlled-output",
                    "\n",
                ):
                    self.assertNotIn(forbidden, serialized)

    def test_provider_free_workload_log_cannot_forge_runner_classification(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        exp_dir = self.repo_root / "outputs" / handle
        diagnostic_log = protocol.log_path(self.state_root, handle)
        diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_log.write_text(
            "provider-free-runner: provider-free execution profile is missing "
            "or stale\n"
            "provider-free-runner: unsafe provider-free output path: "
            "real post-workload revalidation failure\n",
            encoding="utf-8",
        )

        def fake_run(*_args, **_kwargs):
            exp_dir.mkdir(parents=True)
            return 2, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(
            public["bootstrap_diagnostic"],
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-artifact-manifest",
                "classification": "runner-exited-before-artifact-manifest",
                "process_exit_code": 2,
            },
        )

    def test_public_bootstrap_diagnostic_rejects_invalid_closed_state(self) -> None:
        invalid_diagnostics = (
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/2",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "during-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "attacker-selected-classification",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": True,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 256,
            },
        )

        for index, diagnostic in enumerate(invalid_diagnostics):
            with self.subTest(diagnostic=diagnostic):
                group = f"20260805-18{index:04d}-audit"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                protocol.transition(self.state_root, handle, "running")
                protocol.transition(
                    self.state_root,
                    handle,
                    "failed",
                    process_exit_code=2,
                    bootstrap_diagnostic=diagnostic,
                )

                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )

                self.assertNotIn("bootstrap_diagnostic", public)

    def test_public_bootstrap_diagnostic_ignores_unbounded_extra_fields(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        protocol.transition(self.state_root, handle, "running")
        protocol.transition(
            self.state_root,
            handle,
            "failed",
            process_exit_code=1,
            bootstrap_diagnostic={
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-exited-before-artifact-manifest",
                "process_exit_code": 1,
                "detail": "OPENAI_API_KEY=secret\n" * 500,
                "environment": {"VENUS_TOKEN": "secret"},
            },
        )

        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )

        self.assertEqual(
            public["bootstrap_diagnostic"],
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-exited-before-artifact-manifest",
                "process_exit_code": 1,
            },
        )
        self.assertNotIn("secret", json.dumps(public))

    def test_provider_free_supervisor_rejects_changed_sandbox_contract(self) -> None:
        for mutation in ("limit", "extra-bind", "browser-bind", "environment"):
            with self.subTest(mutation=mutation):
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                def fake_run(*_args, **_kwargs):
                    self.write_provider_free_terminal_evidence(handle)
                    exp_dir = self.repo_root / "outputs" / handle
                    sandbox_path = exp_dir / "run/sandbox-enforcement.json"
                    sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
                    if mutation == "limit":
                        sandbox["sandbox_profile"]["resource_limits"][
                            "cpu_seconds"
                        ] = 1
                    elif mutation == "extra-bind":
                        sandbox["argv"][1:1] = ["--bind", "/", "/workspace/repo"]
                    elif mutation == "browser-bind":
                        sandbox["argv"].remove(
                            next(
                                value
                                for value in sandbox["argv"]
                                if "/.cvm-provider-free-browser-stages/"
                                in value
                            )
                        )
                    else:
                        sandbox["required_environment"][
                            "PLAYWRIGHT_BROWSERS_PATH"
                        ] = "/tmp"
                    sandbox_path.write_text(
                        json.dumps(sandbox, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                    proof_path = exp_dir / "run/provider-free-execution.json"
                    proof = json.loads(proof_path.read_text(encoding="utf-8"))
                    proof["sandbox_enforcement"]["sha256"] = hashlib.sha256(
                        sandbox_path.read_bytes()
                    ).hexdigest()
                    proof_path.write_text(
                        json.dumps(proof, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                    manifest_path = exp_dir / "artifact_manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for relative in (
                        "run/sandbox-enforcement.json",
                        "run/provider-free-execution.json",
                    ):
                        data = (exp_dir / relative).read_bytes()
                        entry = next(
                            item
                            for item in manifest["files"]
                            if item["path"] == relative
                        )
                        entry.update(
                            size_bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                        )
                    manifest_path.write_text(
                        json.dumps(manifest, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    return 0, 4321

                with mock.patch.object(
                    runtime, "_run_with_heartbeat", side_effect=fake_run
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={
                            "PATH": os.environ["PATH"],
                            "HOME": os.fspath(self.workspace),
                            "VENUS_TOKEN": "stripped",
                            "OPENAI_API_KEY": "stripped",
                            "ANTHROPIC_API_KEY": "stripped",
                            "HTTPS_PROXY": "stripped",
                        },
                    )

                self.assertEqual(state["state"], "failed")
                self.assertIn("sandbox enforcement", state["failure_reason"])

    def test_provider_free_supervisor_rejects_tampered_preview_sandbox(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle)
            exp_dir = self.repo_root / "outputs" / handle
            preview_path = exp_dir / protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            preview["capabilities"] = "inherit"
            preview_path.write_text(
                json.dumps(preview, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = preview_path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"] == protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH
            )
            entry.update(
                size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "HOME": os.fspath(self.workspace),
                    "VENUS_TOKEN": "stripped",
                    "OPENAI_API_KEY": "stripped",
                    "ANTHROPIC_API_KEY": "stripped",
                    "HTTPS_PROXY": "stripped",
                },
            )

        self.assertEqual(state["state"], "failed")
        self.assertIn("preview sandbox evidence", state["failure_reason"])

    def test_provider_free_supervisor_rejects_tampered_browser_exec_diagnostic(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, stripped=[])
            exp_dir = self.repo_root / "outputs" / handle
            path = exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            diagnostic = json.loads(path.read_text(encoding="utf-8"))
            diagnostic["stdout"] = "sensitive raw browser output"
            path.write_text(
                json.dumps(diagnostic, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"]
                == protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            )
            entry.update(
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertIn("browser exec diagnostic", state["failure_reason"])

    def test_provider_free_supervisor_rejects_duplicate_browser_exec_field(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, stripped=[])
            exp_dir = self.repo_root / "outputs" / handle
            path = exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            original = path.read_text(encoding="utf-8")
            path.write_text(
                original.replace(
                    '"prelaunched_cdp":"passed"',
                    '"prelaunched_cdp":"failed","prelaunched_cdp":"passed"',
                ),
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"]
                == protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
            )
            entry.update(
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertIn(
            "browser exec diagnostic evidence is invalid",
            state["failure_reason"],
        )

    def test_provider_free_supervisor_rejects_duplicate_terminal_manifest_field(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, stripped=[])
            manifest_path = (
                self.repo_root / "outputs" / handle / "artifact_manifest.json"
            )
            original = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                original.replace(
                    '"final_status": 0',
                    '"final_status": 1, "final_status": 0',
                ),
                encoding="utf-8",
            )
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertIn("artifact manifest invalid", state["failure_reason"])

    def test_provider_free_supervisor_rejects_tampered_preview_public_wrapper(
        self,
    ) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]

        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, stripped=[])
            exp_dir = self.repo_root / "outputs" / handle
            path = exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            wrapper = json.loads(path.read_text(encoding="utf-8"))
            wrapper["stderr"] = "sensitive raw public output"
            path.write_text(
                json.dumps(wrapper, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = path.read_bytes()
            entry = next(
                item
                for item in manifest["files"]
                if item["path"]
                == protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            )
            entry.update(
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={"PATH": os.environ["PATH"]},
            )

        self.assertEqual("failed", state["state"])
        self.assertIn("preview public wrapper", state["failure_reason"])

    def test_submit_launch_failure_is_terminal(self) -> None:
        def fail_detach(handle, command, root):
            raise OSError("no process")

        result = runtime.submit_pilot(
            "airplane",
            self.group,
            state_root=self.state_root,
            detach=fail_detach,
        )
        state = protocol.load_state(self.state_root, result["job"])
        self.assertEqual(state["state"], "failed")
        self.assertIn("launch failed", state["failure_reason"])

    def test_submit_rejects_preexisting_log_without_launch_or_overwrite(self) -> None:
        fixed = datetime(2026, 8, 5, 17, 0, 0, tzinfo=timezone.utc)
        cases = (
            ("pilot", "airplane", "20260805-170001-log-collision"),
            (
                "provider-free",
                "issue15-runtime-authority",
                "20260805-170002-log-collision",
            ),
        )
        original = (
            b"provider-free-runner: provider-free execution profile is missing "
            b"or stale\nOPENAI_API_KEY=must-remain-retained\n"
        )

        for kind, object_name, group in cases:
            with self.subTest(kind=kind):
                handle = f"{group}/20260805-170000-{object_name}"
                destination = protocol.log_path(self.state_root, handle)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(original)
                destination.chmod(0o640)

                with mock.patch.object(runtime, "datetime") as clock:
                    clock.now.return_value = fixed
                    with mock.patch.object(runtime.subprocess, "Popen") as popen:
                        if kind == "pilot":
                            result = runtime.submit_pilot(
                                object_name,
                                group,
                                state_root=self.state_root,
                            )
                        else:
                            result = runtime.submit_provider_free(
                                object_name,
                                group,
                                state_root=self.state_root,
                            )

                state = protocol.load_state(self.state_root, handle)
                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )
                self.assertEqual(result["state"], "failed")
                self.assertEqual(state["state"], "failed")
                self.assertEqual(
                    state["failure_reason"],
                    "supervisor launch failed: FileExistsError",
                )
                popen.assert_not_called()
                self.assertEqual(destination.read_bytes(), original)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o640)
                self.assertNotIn("bootstrap_diagnostic", public)

    def test_submit_exclusively_creates_private_log_before_launch(self) -> None:
        fixed = datetime(2026, 8, 5, 17, 0, 0, tzinfo=timezone.utc)
        cases = (
            ("pilot", "airplane", "20260805-170003-private-log"),
            (
                "provider-free",
                "issue15-runtime-authority",
                "20260805-170004-private-log",
            ),
        )

        for kind, object_name, group in cases:
            with self.subTest(kind=kind):
                handle = f"{group}/20260805-170000-{object_name}"
                destination = protocol.log_path(self.state_root, handle)

                with mock.patch.object(runtime, "datetime") as clock:
                    clock.now.return_value = fixed
                    with mock.patch.object(runtime.subprocess, "Popen") as popen:
                        popen.return_value.pid = 1234
                        if kind == "pilot":
                            result = runtime.submit_pilot(
                                object_name,
                                group,
                                state_root=self.state_root,
                            )
                        else:
                            result = runtime.submit_provider_free(
                                object_name,
                                group,
                                state_root=self.state_root,
                            )

                state = protocol.load_state(self.state_root, handle)
                self.assertEqual(result["state"], "submitted")
                self.assertEqual(state["state"], "submitted")
                popen.assert_called_once()
                self.assertEqual(destination.read_bytes(), b"")
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_group_allocation_lock_serializes_handle_creation(self) -> None:
        fixed = datetime(2026, 8, 5, 17, 0, 0, tzinfo=timezone.utc)
        completed = threading.Event()
        result: dict[str, object] = {}

        def submit_second() -> None:
            result.update(
                runtime.submit_pilot(
                    "airplane",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1,
                )
            )
            completed.set()

        with mock.patch.object(runtime, "datetime") as clock:
            clock.now.return_value = fixed
            with runtime._allocation_lock(self.state_root, self.group):
                thread = threading.Thread(target=submit_second)
                thread.start()
                time.sleep(0.05)
                self.assertFalse(completed.is_set())
                first_exp = runtime._allocate_exp(
                    "airplane", self.group, self.state_root
                )
                protocol.publish_state(
                    self.state_root,
                    runtime._pilot_record(
                        "airplane", self.group, first_exp, self.state_root
                    ),
                )
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            result["job"],
            f"{self.group}/20260805-170000-airplane-2",
        )

    def test_submit_rejects_group_that_pilot_runner_cannot_use(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "invalid pilot group"):
            runtime.submit_pilot(
                "airplane",
                "group",
                state_root=self.state_root,
                detach=lambda *args: 1,
            )
        self.assertFalse((self.state_root / "pilots").exists())

    def test_provider_free_allocation_rejects_symlinked_output_components(self) -> None:
        fixed = datetime(2026, 8, 11, 22, 30, 0, tzinfo=timezone.utc)
        outside = self.workspace / "outside"
        outside.mkdir()
        outputs = self.repo_root / "outputs"
        for mutation in ("group", "exp"):
            with self.subTest(mutation=mutation):
                group = f"20260805-170000-audit-{mutation}"
                group_path = outputs / group
                try:
                    if mutation == "group":
                        group_path.symlink_to(outside, target_is_directory=True)
                    else:
                        group_path.mkdir()
                        (
                            group_path
                            / "20260811-223000-issue15-runtime-authority"
                        ).symlink_to(outside, target_is_directory=True)
                    with (
                        mock.patch.object(runtime, "datetime") as clock,
                        self.assertRaisesRegex(protocol.ProtocolError, "output path"),
                    ):
                        clock.now.return_value = fixed
                        runtime.submit_provider_free(
                            "issue15-runtime-authority",
                            group,
                            state_root=self.state_root,
                            detach=lambda *args: 1234,
                        )
                    self.assertEqual([], list(outside.iterdir()))
                    self.assertFalse((self.state_root / "pilots" / group).exists())
                finally:
                    if group_path.is_symlink():
                        group_path.unlink()
                    elif group_path.exists():
                        for child in group_path.iterdir():
                            child.unlink()
                        group_path.rmdir()

    def test_provider_free_supervisor_revalidates_output_before_subprocess(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        group_path = self.repo_root / "outputs" / self.group
        outside = self.workspace / "outside-supervisor"
        outside.mkdir()
        group_path.rmdir()
        group_path.symlink_to(outside, target_is_directory=True)
        try:
            with mock.patch.object(runtime, "_run_with_heartbeat") as run:
                state = runtime.supervise_provider_free(
                    handle,
                    state_root=self.state_root,
                    environ={"PATH": os.environ["PATH"]},
                )
            run.assert_not_called()
            self.assertEqual("failed", state["state"])
            self.assertEqual([], list(outside.iterdir()))
        finally:
            group_path.unlink()

    def test_provider_free_poisoned_exact_exp_is_terminal_without_subprocess(
        self,
    ) -> None:
        for mutation in ("empty", ".git", "run", ".gitignore"):
            with self.subTest(mutation=mutation):
                group = f"20260805-170000-poison-{mutation.strip('.')}"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                state = protocol.load_state(self.state_root, handle)
                exp_dir = self.repo_root / state["exp_dir"]
                exp_dir.mkdir()
                outside = self.workspace / f"outside-{mutation.strip('.')}"
                if mutation == ".gitignore":
                    outside.write_text("sentinel\n", encoding="utf-8")
                    (exp_dir / mutation).symlink_to(outside)
                elif mutation != "empty":
                    outside.mkdir()
                    (exp_dir / mutation).symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                try:
                    with mock.patch.object(
                        runtime,
                        "_run_with_heartbeat",
                        return_value=(2, 4321),
                    ) as run:
                        terminal = runtime.supervise_provider_free(
                            handle,
                            state_root=self.state_root,
                            environ={"PATH": os.environ["PATH"]},
                        )
                    run.assert_not_called()
                    self.assertEqual("failed", terminal["state"])
                    self.assertEqual(
                        terminal,
                        protocol.load_state(self.state_root, handle),
                    )
                    with self.assertRaisesRegex(
                        protocol.ProtocolError,
                        "cannot start from failed",
                    ):
                        runtime.supervise_provider_free(
                            handle,
                            state_root=self.state_root,
                            environ={"PATH": os.environ["PATH"]},
                        )
                    if mutation == ".gitignore":
                        self.assertEqual(
                            "sentinel\n",
                            outside.read_text(encoding="utf-8"),
                        )
                    elif mutation != "empty":
                        self.assertEqual([], list(outside.iterdir()))
                finally:
                    shutil.rmtree(exp_dir)
                    exp_dir.parent.rmdir()

    def test_exit_zero_without_manifest_fails(self) -> None:
        handle = self.submit()
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["process_exit_code"], 0)
        self.assertIn("manifest missing", state["failure_reason"])

    def test_exit_zero_and_manifest_zero_succeeds(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(state["runner_final_status"], 0)
        self.assertIsNotNone(state["heartbeat_at"])
        self.assertIsInstance(state["pilot_pid"], int)

    def test_default_pilot_command_uses_allocated_exp_name(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        captured: dict[str, object] = {}

        def fake_run(root, job, command, **kwargs):
            captured["command"] = list(command)
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
            )

        parsed = protocol.parse_handle(handle)
        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(
            captured["command"],
            [
                os.fspath(runtime.PILOT_SCRIPT),
                "airplane",
                self.group,
                parsed["exp"],
            ],
        )

    def test_heartbeat_updates_while_child_is_running(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        with mock.patch.object(runtime, "heartbeat", wraps=runtime.heartbeat) as beat:
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
                interval=0.005,
                command=[sys.executable, "-c", "import time; time.sleep(0.04)"],
            )
        self.assertEqual(state["state"], "succeeded")
        self.assertGreaterEqual(beat.call_count, 2)

    def test_supervisor_error_terminates_started_pilot_process_group(self) -> None:
        handle = self.submit()
        pid_path = self.workspace / "pilot.pid"
        calls = 0

        def fail_heartbeat(*args, **kwargs) -> None:
            nonlocal calls
            calls += 1
            if calls < 2:
                return
            deadline = time.monotonic() + 1
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            raise OSError("state storage unavailable")

        command = [
            sys.executable,
            "-c",
            (
                "import os,time; "
                f"open({os.fspath(pid_path)!r}, 'w').write(str(os.getpid())); "
                "time.sleep(2)"
            ),
        ]
        with mock.patch.object(runtime, "heartbeat", side_effect=fail_heartbeat):
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
                interval=0.001,
                command=command,
            )

        self.assertEqual(state["state"], "failed")
        self.assertIn("supervisor error", state["failure_reason"])
        pilot_pid = int(pid_path.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pilot_pid, 0)

    def test_process_group_cleanup_kills_descendants_after_leader_exits(self) -> None:
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = 0
        process.wait.return_value = 0

        with (
            mock.patch.object(runtime.os, "killpg") as killpg,
            mock.patch.object(runtime.time, "monotonic", side_effect=(10.0, 10.0)),
            mock.patch.object(runtime.time, "sleep") as sleep,
        ):
            runtime._terminate_process_group(process, grace=0.01)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.01)
        process.wait.assert_not_called()
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4321, runtime.signal.SIGTERM),
                mock.call(4321, 0),
                mock.call(4321, 0),
                mock.call(4321, runtime.signal.SIGKILL),
            ],
        )

    def test_process_group_cleanup_reaps_real_descendant_after_leader_exit(
        self,
    ) -> None:
        pid_path = self.workspace / "descendant.pid"
        child_code = (
            "import os,time; "
            f"open({os.fspath(pid_path)!r}, 'w').write(str(os.getpid())); "
            "time.sleep(30)"
        )
        leader_code = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", leader_code],
            start_new_session=True,
        )
        process.wait(timeout=2)
        deadline = time.monotonic() + 1
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(pid_path.exists())

        try:
            runtime._terminate_process_group(process, grace=0.01)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.005)
            else:
                self.fail("descendant process group survived supervisor cleanup")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_nonzero_process_or_manifest_fails(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "raise SystemExit(7)"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["process_exit_code"], 7)

        handle = runtime.submit_pilot(
            "chair",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1,
        )["job"]
        self.write_manifest(handle, 4)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["runner_final_status"], 4)

    def test_invalid_manifest_status_types_fail_closed(self) -> None:
        for index, final_status in enumerate((True, "0", None)):
            with self.subTest(final_status=final_status):
                handle = runtime.submit_pilot(
                    f"chair-{index}",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1,
                )["job"]
                self.write_manifest(handle, final_status)
                state = runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    interval=0.001,
                    command=[sys.executable, "-c", "pass"],
                )
                self.assertEqual(state["state"], "failed")
                self.assertIsNone(state["runner_final_status"])
                self.assertIn("not an integer", state["failure_reason"])

    def test_wait_reads_observation_only_when_returning(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        calls = 0

        def sleeper(_seconds: float) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    interval=0.001,
                    command=[sys.executable, "-c", "pass"],
                )

        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "ready"}}) as observe:
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                timeout=10,
                poll_interval=0,
                sleeper=sleeper,
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "succeeded")
        observe.assert_called_once()

    def test_wait_stale_and_timeout_do_not_mutate_state(self) -> None:
        handle = self.submit()
        state = protocol.load_state(self.state_root, handle)
        state["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
        protocol.publish_state(self.state_root, state)
        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "pending"}}):
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                until="terminal-or-stale",
                stale_after=1,
                timeout=10,
            )
        self.assertEqual(code, 3)
        self.assertEqual(result["health"], "stale")
        self.assertEqual(protocol.load_state(self.state_root, handle)["state"], "submitted")

        ticks = iter((0.0, 2.0, 2.0))
        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "pending"}}):
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                timeout=1,
                poll_interval=0,
                clock=lambda: next(ticks),
                sleeper=lambda _: None,
            )
        self.assertEqual(code, 4)
        self.assertEqual(result["wait"], "timeout")
        self.assertEqual(protocol.load_state(self.state_root, handle)["state"], "submitted")

    def test_tap_observer_exception_does_not_change_terminal_result(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        with mock.patch.object(runtime.tap_observer, "observe_exp", side_effect=RuntimeError("bad tap")):
            result = runtime.status_job(handle, state_root=self.state_root)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["observation"]["tap"]["availability"], "degraded")

    def test_concurrent_supervisor_start_is_rejected_by_lock(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        started = threading.Event()
        release = threading.Event()
        first_result: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release first supervisor")
            return 0, 4321

        def run_first() -> None:
            first_result.update(
                runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    command=[sys.executable, "-c", "pass"],
                )
            )

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            thread = threading.Thread(target=run_first)
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            try:
                with self.assertRaisesRegex(
                    protocol.ProtocolError, "supervisor already running"
                ):
                    runtime.supervise_pilot(
                        handle,
                        state_root=self.state_root,
                        command=[sys.executable, "-c", "pass"],
                    )
            finally:
                release.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first_result["state"], "succeeded")

    def test_cli_rejects_missing_job_with_compact_json(self) -> None:
        env = {**os.environ, "CVM_JOB_STATE_ROOT": os.fspath(self.state_root)}
        result = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "status", "group/missing"],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])

    def test_cli_status_and_wait_preserve_terminal_exit_codes(self) -> None:
        env = {**os.environ, "CVM_JOB_STATE_ROOT": os.fspath(self.state_root)}
        cwd = Path(__file__).resolve().parents[3]
        handles = []
        for object_name, terminal in (("airplane", "succeeded"), ("chair", "failed")):
            handle = runtime.submit_pilot(
                object_name,
                self.group,
                state_root=self.state_root,
                detach=lambda *args: 1,
            )["job"]
            protocol.transition(self.state_root, handle, "running")
            protocol.transition(self.state_root, handle, terminal)
            handles.append(handle)

        status = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "status", handles[0]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        succeeded = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "wait", handles[0]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        failed = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "wait", handles[1]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(json.loads(succeeded.stdout)["state"], "succeeded")
        failed_payload = json.loads(failed.stdout)
        self.assertEqual(failed_payload["state"], "failed")
        self.assertIn("process_exit_code", failed_payload)

    def test_submit_and_monitor_are_single_ssh_wrappers(self) -> None:
        submit = SUBMIT_SCRIPT.read_text(encoding="utf-8")
        monitor = MONITOR_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(submit.count("exec ssh"), 1)
        self.assertEqual(monitor.count("exec ssh"), 1)
        self.assertIn("ServerAliveInterval=30", monitor)
        self.assertIn("ServerAliveCountMax=6", monitor)
        self.assertIn("scripts.pilot.cvm_job status", monitor)
        self.assertIn("scripts.pilot.cvm_job wait", monitor)
        for forbidden in (" ps ", " stat ", " find ", "git log"):
            self.assertNotIn(forbidden, monitor)

    def test_monitor_contract_documents_bounded_bootstrap_diagnostic(self) -> None:
        contract = MONITOR_SKILL.read_text(encoding="utf-8")

        self.assertIn("cvm.provider-free-bootstrap-diagnostic/1", contract)
        self.assertIn("cvm.provider-free-scenario-failure/1", contract)
        self.assertIn("scenario_failure", contract)
        for stage in (
            "viewer_deployment",
            "shipped_tree",
            "cadpy_runtime",
            "viewer_fallback",
            "candidate_workspace",
            "native_measurement",
            "finalization",
        ):
            self.assertIn(stage, contract)
        for operation in (
            "fixture_availability",
            "canonical_build",
            "reference_preparation",
            "workspace_init",
        ):
            self.assertIn(operation, contract)
        self.assertIn("before-experiment", contract)
        self.assertIn("before-artifact-manifest", contract)
        for classification in (
            "python-import-failed",
            "runner-execution-profile-rejected",
            "runner-environment-allowlist-rejected",
            "runner-stripped-name-receipt-rejected",
            "runner-request-digest-rejected",
            "runner-bwrap-path-rejected",
            "runner-runtime-identity-rejected",
            "runner-output-path-rejected",
            "runner-contract-rejected",
            "runner-entrypoint-unavailable",
            "runner-exited-before-artifact-manifest",
            "runner-terminated-before-artifact-manifest",
            "runner-completed-without-artifact-manifest",
        ):
            self.assertIn(classification, contract)
        self.assertIn("4 KiB", contract)
        self.assertIn("does not publish raw log text", contract)

    def test_submit_contract_documents_exclusive_private_job_log(self) -> None:
        contract = SUBMIT_SKILL.read_text(encoding="utf-8")

        self.assertIn("atomically creates", contract)
        self.assertIn("`0600`", contract)
        self.assertIn("supervisor launch failed", contract)
        self.assertIn("retained unchanged", contract)
        self.assertIn("no supervisor starts", contract)

    def test_submit_and_monitor_forward_one_approved_remote_cli_call(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        command_log = self.workspace / "commands.log"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_WRAPPER_LOG"
printf '%s\\n' '{"job":"group/exp","state":"submitted"}'
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_WRAPPER_LOG": os.fspath(command_log),
        }
        submitted = subprocess.run(
            [
                os.fspath(SUBMIT_SCRIPT),
                "pilot",
                "airplane",
                "20260805-170000-audit",
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitored = subprocess.run(
            [os.fspath(MONITOR_SCRIPT), "--wait", "--timeout", "3", "group/exp"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(monitored.returncode, 0, monitored.stderr)
        self.assertEqual(len(submitted.stdout.splitlines()), 1)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 2)
        self.assertIn("scripts.pilot.cvm_job submit-pilot", commands[0])
        self.assertIn("20260805-170000-audit", commands[0])
        self.assertIn("ServerAliveInterval=30", commands[1])
        self.assertIn("scripts.pilot.cvm_job wait", commands[1])

    def test_provider_free_submit_forwards_only_the_allowlisted_scenario(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        command_log = self.workspace / "provider-free-commands.log"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_WRAPPER_LOG"
printf '%s\\n' '{"job":"group/exp","state":"submitted"}'
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_WRAPPER_LOG": os.fspath(command_log),
        }

        submitted = subprocess.run(
            [
                os.fspath(SUBMIT_SCRIPT),
                "provider-free",
                "issue15-runtime-authority",
                "20260811-210000-issue15-provider-free",
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 1)
        self.assertIn(
            "scripts.pilot.cvm_job submit-provider-free "
            "'issue15-runtime-authority' "
            "'20260811-210000-issue15-provider-free'",
            commands[0],
        )

    def test_provider_free_submit_rejects_commands_paths_and_unsafe_groups_before_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "provider-free-ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        invalid = (
            ("provider-free", "../../bin/sh", "20260811-210000-safe"),
            ("provider-free", "echo${IFS}owned", "20260811-210000-safe"),
            ("provider-free", "issue15-runtime-authority", "../unsafe"),
            (
                "provider-free",
                "issue15-runtime-authority",
                "20260811-210000-safe",
                "extra-command",
            ),
        )
        for argv in invalid:
            result = subprocess.run(
                [os.fspath(SUBMIT_SCRIPT), *argv],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, argv)
        self.assertFalse(marker.exists())

    def test_submit_and_monitor_reject_batch_without_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        submitted = subprocess.run(
            [os.fspath(SUBMIT_SCRIPT), "batch", "group", "airplane"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitored = subprocess.run(
            [os.fspath(MONITOR_SCRIPT), "--once", "batch/group"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 2)
        self.assertEqual(monitored.returncode, 2)
        self.assertFalse(marker.exists())

    def test_submit_rejects_noncanonical_group_without_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        submitted = subprocess.run(
            [os.fspath(SUBMIT_SCRIPT), "pilot", "airplane", "group"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 2)
        self.assertFalse(marker.exists())

    def test_legacy_batch_entrypoint_still_calls_toys4k_pilot(self) -> None:
        repo = self.workspace / "batch-repo"
        script_dir = repo / "scripts" / "pilot"
        script_dir.mkdir(parents=True)
        batch_script = script_dir / "toys4k-batch.sh"
        shutil.copy2(PILOT_ROOT / "toys4k-batch.sh", batch_script)
        batch_script.chmod(0o755)
        command_log = self.workspace / "batch-commands.log"
        self.write_executable(
            script_dir / "toys4k-pilot.sh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_BATCH_TEST_LOG"
""",
        )
        secrets = self.workspace / "secrets.env"
        secrets.write_text("VENUS_TOKENS=(secret-one)\n", encoding="utf-8")
        env = {
            **os.environ,
            "TEXT_TO_CAD_SECRETS": os.fspath(secrets),
            "CVM_BATCH_TEST_LOG": os.fspath(command_log),
        }
        result = subprocess.run(
            [os.fspath(batch_script), "legacy", "airplane", "chair"],
            cwd=repo,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 2)
        self.assertEqual({line.split()[0] for line in commands}, {"airplane", "chair"})
        self.assertNotIn("secret-one", command_log.read_text(encoding="utf-8"))

    @staticmethod
    def write_executable(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
