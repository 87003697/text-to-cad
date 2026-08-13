from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot import deployment_authority
from scripts.pilot import provider_free_runner
from scripts.pilot.cvm_job import protocol


class ProviderFreeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-free-runner-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        (self.repo / "outputs").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").write_text("", encoding="utf-8")
        for declared in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            path = self.repo / declared
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{declared}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "authority-marker.txt").write_text(
                    f"{declared}\n", encoding="utf-8"
                )
        cadpy = self.repo / deployment_authority.CADPY_RUNTIME_PATH
        cadpy.parent.mkdir(parents=True, exist_ok=True)
        cadpy.write_text("cadpy\n", encoding="utf-8")
        self.bwrap = self.repo / "trusted/usr/bin/bwrap"
        self.bwrap.parent.mkdir(parents=True)
        self.bwrap.write_text(
            "#!/bin/sh\nprintf 'bubblewrap 1.2.3\\n'\n",
            encoding="utf-8",
        )
        self.bwrap.chmod(0o755)
        self.bwrap = self.bwrap.resolve(strict=True)
        self.browser_cache = Path(self.temporary.name) / "trusted/ms-playwright"
        self.browser = (
            self.browser_cache
            / "chromium_headless_shell-1234/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        self.browser.parent.mkdir(parents=True)
        self.browser.write_bytes(b"trusted-browser")
        self.browser.chmod(0o755)
        self.browser = self.browser.resolve(strict=True)
        self.browser_cache = self.browser_cache.resolve(strict=True)
        self.trusted_bwrap_patch = mock.patch.object(
            deployment_authority,
            "TRUSTED_BWRAP_PATH",
            self.bwrap,
        )
        self.trusted_bwrap_patch.start()
        self.addCleanup(self.trusted_bwrap_patch.stop)
        self.runtime_identity = {
            "schema": "cvm.provider-free-runtime-identity/1",
            "bwrap": {
                "path": os.fspath(self.bwrap),
                "sha256": hashlib.sha256(self.bwrap.read_bytes()).hexdigest(),
                "version": "bubblewrap 1.2.3",
            },
            "chromium": {
                "revision": "1234",
                "host_cache_path": os.fspath(self.browser_cache),
                "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                "executable_path": os.fspath(self.browser),
                "sha256": hashlib.sha256(self.browser.read_bytes()).hexdigest(),
            },
            "cadpy": {
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
            },
        }
        deployed_receipt = deployment_authority.write_receipt(
            self.repo,
            source_head="a" * 40,
            runtime_identity=self.runtime_identity,
        )
        deployment_receipt_bytes = (
            self.repo / deployment_authority.RECEIPT_PATH
        ).read_bytes()
        self.group = "20260811-210000-issue15-provider-free"
        self.exp = "20260811-210001-issue15-runtime-authority"
        self.handle = f"{self.group}/{self.exp}"
        immutable_request = {
            "job_kind": "provider-free",
            "object": "issue15-runtime-authority",
            "group": self.group,
            "exp": self.exp,
            "exp_dir": f"outputs/{self.handle}",
            "scenario": {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
            "execution_profile": {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/15",
                "provider_access": "forbidden",
            },
            "request_authority": {
                "schema": "cvm.provider-free-request-authority/1",
                "deployment_receipt": deployment_authority.RECEIPT_PATH,
                "deployment_receipt_sha256": hashlib.sha256(
                    deployment_receipt_bytes
                ).hexdigest(),
                "deployment_receipt_canonical_sha256": hashlib.sha256(
                    json.dumps(
                        deployed_receipt, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "deployment_source_head": deployed_receipt["source_head"],
                "deployment_tree_sha256": deployed_receipt["tree_sha256"],
                "runtime_identity": self.runtime_identity,
            },
        }
        self.environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/test",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CVM_PROVIDER_FREE_PROFILE": "issue15.provider-free-bounded/15",
            "CVM_PROVIDER_FREE_STRIPPED_NAMES": (
                "ANTHROPIC_API_KEY,HTTPS_PROXY,OPENAI_API_KEY,VENUS_TOKEN"
            ),
            "CVM_PROVIDER_FREE_JOB": self.handle,
            "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256": (
                protocol.request_authority_sha256(immutable_request)
            ),
            "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256": deployed_receipt[
                "tree_sha256"
            ],
            "CVM_PROVIDER_FREE_REQUEST_JSON": json.dumps(
                immutable_request, sort_keys=True, separators=(",", ":")
            ),
        }

    def test_interpreter_owned_startup_environment_is_platform_closed(self) -> None:
        cases = (
            ("linux", {"LC_CTYPE": "C.UTF-8"}),
            ("darwin", {"LC_CTYPE": "UTF-8"}),
            (
                "darwin",
                {"__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0"},
            ),
            (
                "darwin",
                {"__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x19:0x34"},
            ),
        )

        for platform_name, startup_environment in cases:
            with self.subTest(
                platform=platform_name,
                startup_environment=startup_environment,
            ), mock.patch.object(provider_free_runner.sys, "platform", platform_name):
                environment = {**self.environment, **startup_environment}
                self.assertEqual(
                    provider_free_runner._validate_environment(environment),
                    [
                        "ANTHROPIC_API_KEY",
                        "HTTPS_PROXY",
                        "OPENAI_API_KEY",
                        "VENUS_TOKEN",
                    ],
                )

    def test_interpreter_environment_rejects_invalid_values_and_extra_names(self) -> None:
        cf_user = f"0x{os.getuid():X}"
        cases = (
            ("linux", {"LC_CTYPE": "UTF-8"}),
            ("darwin", {"LC_CTYPE": "C.UTF-8"}),
            ("freebsd", {"LC_CTYPE": "C.UTF-8"}),
            ("linux", {"__CF_USER_TEXT_ENCODING": f"{cf_user}:0x19:0x34"}),
            ("darwin", {"__CF_USER_TEXT_ENCODING": "0x0:0x19:0x34"}),
            (
                "darwin",
                {
                    "__CF_USER_TEXT_ENCODING": (
                        f"{cf_user.replace('0x', '0X')}:0x19:0x34"
                    )
                },
            ),
            ("darwin", {"__CF_USER_TEXT_ENCODING": f"{cf_user}:0x0:0x34"}),
            ("darwin", {"__CF_USER_TEXT_ENCODING": f"{cf_user}:0x19:0x0"}),
            ("linux", {"PYTHONPATH": "/host/injection"}),
            ("linux", {"HTTPS_PROXY": "http://provider.invalid"}),
            ("linux", {"VENUS_TOKEN": "provider-secret"}),
            ("linux", {"lc_ctype": "C.UTF-8"}),
            ("darwin", {"__CF_USER_TEXT_ENCODING_": f"{cf_user}:0x19:0x34"}),
        )

        for platform_name, hostile_environment in cases:
            with self.subTest(
                platform=platform_name,
                hostile_environment=hostile_environment,
            ), mock.patch.object(provider_free_runner.sys, "platform", platform_name):
                with self.assertRaisesRegex(
                    provider_free_runner.ProviderFreeError,
                    "invalid|non-allowlisted",
                ):
                    provider_free_runner._validate_environment(
                        {**self.environment, **hostile_environment}
                    )

    def test_supervisor_locale_is_exact_and_interpreter_names_are_control_only(self) -> None:
        for locale_mutation in (
            {"LANG": "en_US.UTF-8"},
            {"LC_ALL": "C"},
            {"LANG": "C.UTF-8", "LC_ALL": ""},
        ):
            with self.subTest(locale_mutation=locale_mutation):
                with self.assertRaisesRegex(
                    provider_free_runner.ProviderFreeError,
                    "deterministic locale",
                ):
                    provider_free_runner._validate_environment(
                        {**self.environment, **locale_mutation}
                    )

        control_environment = {
            **self.environment,
            "LC_CTYPE": "UTF-8",
            "__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x19:0x34",
        }
        sandbox_environment = provider_free_runner._sandbox_environment(
            control_environment
        )
        for control_name in (
            "LC_ALL",
            "LC_CTYPE",
            "__CF_USER_TEXT_ENCODING",
        ):
            self.assertNotIn(control_name, sandbox_environment)
        self.assertEqual(sandbox_environment["LANG"], "C.UTF-8")

        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True)
        (exp_dir / "run/sandbox-enforcement.json").write_text(
            "{}\n", encoding="utf-8"
        )
        provider_free_runner._publish_no_provider_proof(
            exp_dir,
            handle=self.handle,
            scenario_name="issue15-runtime-authority",
            stripped=["LC_CTYPE", "__CF_USER_TEXT_ENCODING"],
            environ=control_environment,
        )
        proof_text = (exp_dir / "run/provider-free-execution.json").read_text(
            encoding="utf-8"
        )
        proof = json.loads(proof_text)
        self.assertNotIn(
            "LC_CTYPE", proof["provider_environment"]["allowlist"]
        )
        self.assertNotIn(
            "__CF_USER_TEXT_ENCODING",
            proof["provider_environment"]["allowlist"],
        )
        self.assertEqual(
            proof["provider_environment"]["stripped"],
            ["LC_CTYPE", "__CF_USER_TEXT_ENCODING"],
        )
        self.assertNotIn(control_environment["__CF_USER_TEXT_ENCODING"], proof_text)

    def write_success_evidence(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True, exist_ok=True)
        (exp_dir / "run/provider-free-commands.jsonl").write_text(
            '{"exit_code":0}\n', encoding="utf-8"
        )
        (exp_dir / protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
                    "argv": protocol.provider_free_preview_sandbox_argv(
                        self.group, self.exp
                    ),
                    "capabilities": "drop-all",
                    "mount_namespace": "inherit-outer",
                }
            ),
            encoding="utf-8",
        )
        (exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
                    "executable": (
                        protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
                    ),
                    "probe": "chromium-version-immediate-exit",
                    "outer": "passed",
                    "nested": "passed",
                    "node_attached": "not-run",
                    "node_detached": "not-run",
                    "node_failure_kind": "not-run",
                    "prelaunched_cdp": "passed",
                }
            ),
            encoding="utf-8",
        )
        (exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": "passed",
                }
            ),
            encoding="utf-8",
        )
        preview_path = exp_dir / "steps/000000/preview/preview.json"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(
            json.dumps(
                {
                    "browser_runtime": {
                        "schema": protocol.PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA,
                        "adapter_profile": {
                            "name": protocol.PROVIDER_FREE_BROWSER_ADAPTER_PROFILE,
                            "sha256": protocol.PROVIDER_FREE_BROWSER_ADAPTER_PROFILE_SHA256,
                        },
                        "browser_identity": {
                            "playwright": "1.60.0",
                            "browser": "chromium-headless-shell",
                            "revision": "1223",
                            "version": "Google Chrome for Testing 148.0.7778.96",
                            "sha256": self.runtime_identity["chromium"]["sha256"],
                        },
                        "result": "passed",
                    }
                }
            ),
            encoding="utf-8",
        )
        files = [{"path": "runtime-identity.json", "size_bytes": 1, "sha256": "a" * 64}]
        tree_bytes = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        artifact = {
            "role": "launcher",
            "source": {"path": "viewer/source", "sha256": "a" * 64},
            "bundle": {"path": "skills/viewer/bundle", "sha256": "b" * 64},
            "deployed": {"path": "skills/viewer/bundle", "sha256": "b" * 64},
        }
        _artifacts = [
            artifact,
            {**artifact, "role": "server"},
            {**artifact, "role": "client"},
        ]
        receipt = {
            "schema": "issue15.runtime-authority-smoke/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "workspace": {
                "path": ".",
                "schema": "mesh-to-cad.workspace/1",
                "final_delivery": {"identity_sha256": "a" * 64},
            },
            "viewer_deployment": {
                "schema": "cvm.viewer-runtime-deployment/1",
                "artifacts": _artifacts,
            },
            "viewer_fallback": {
                "schema": "issue15.viewer-fallback-smoke/1",
                "rejected_reuse": {"http_status": 400},
                "fallback": {"action": "start"},
            },
            "native_depth_eight": {
                "native_required": True,
                "backend": {"id": "meshscope.voxblame.native-sat/1"},
                "depths": list(range(1, 9)),
            },
            "cadpy_runtime": {
                "schema": "cvm.audited-cadpy-runtime/1",
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": self.runtime_identity["cadpy"]["sha256"],
            },
            "shipped_tree": {
                "schema": "cvm.deployed-runtime-tree-receipt/1",
                "file_count": 1,
                "tree_sha256": hashlib.sha256(tree_bytes).hexdigest(),
                "files": files,
            },
            "commands": "run/provider-free-commands.jsonl",
            "preview_sandbox": protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
        }
        (exp_dir / "run" / "runtime-authority-smoke.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    def write_authority(self, _exp_dir: Path) -> dict[str, object]:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "workspace-authority.json").write_text("{}\n", encoding="utf-8")
        (exp_dir / "workspace-authority.bundle").write_bytes(b"bundle")
        return {"mode": "live"}

    def test_success_rejects_tampered_preview_sandbox_receipt(self) -> None:
        self.write_success_evidence()
        exp_dir = self.repo / "outputs" / self.handle
        path = exp_dir / protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["capabilities"] = "inherit"
        path.write_text(json.dumps(receipt), encoding="utf-8")

        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "preview sandbox evidence conflicts",
        ):
            provider_free_runner._validate_scenario_evidence(
                exp_dir,
                "issue15-runtime-authority",
                expected_browser_sha256=self.runtime_identity["chromium"]["sha256"],
            )

    def test_success_rejects_tampered_browser_exec_diagnostic(self) -> None:
        self.write_success_evidence()
        self.write_authority(self.repo / "outputs" / self.handle)
        exp_dir = self.repo / "outputs" / self.handle
        path = exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["stdout"] = "sensitive raw version"
        path.write_text(json.dumps(receipt), encoding="utf-8")

        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "browser exec diagnostic conflicts",
        ):
            provider_free_runner._validate_scenario_evidence(
                exp_dir,
                "issue15-runtime-authority",
                expected_browser_sha256=self.runtime_identity["chromium"]["sha256"],
            )

    def test_success_rejects_preview_browser_digest_outside_deployment_authority(self) -> None:
        self.write_success_evidence()
        self.write_authority(self.repo / "outputs" / self.handle)
        exp_dir = self.repo / "outputs" / self.handle
        preview_path = exp_dir / "steps/000000/preview/preview.json"
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        preview["browser_runtime"]["browser_identity"]["sha256"] = "2" * 64
        preview["preview_identity_sha256"] = hashlib.sha256(
            json.dumps(preview, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        preview_path.write_text(json.dumps(preview), encoding="utf-8")

        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "browser runtime evidence conflicts",
        ):
            provider_free_runner._validate_scenario_evidence(
                exp_dir,
                "issue15-runtime-authority",
                expected_browser_sha256=self.runtime_identity["chromium"]["sha256"],
            )

    def test_failure_requires_matching_closed_browser_exec_diagnostic(
        self,
    ) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True)
        (exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
                    "scenario_identity": (
                        "issue15.provider-free.runtime-authority/1"
                    ),
                    "stage": "native_measurement",
                    "operation": "preview_browser_nested_exec_probe",
                }
            ),
            encoding="utf-8",
        )
        (exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
                    "executable": (
                        protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
                    ),
                    "probe": "chromium-version-immediate-exit",
                    "outer": "passed",
                    "nested": "failed",
                    "node_attached": "not-run",
                    "node_detached": "not-run",
                    "node_failure_kind": "not-run",
                    "prelaunched_cdp": "not-run",
                }
            ),
            encoding="utf-8",
        )

        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )
        failure_path = exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        failure["operation"] = "preview_browser_outer_exec_probe"
        failure_path.write_text(json.dumps(failure), encoding="utf-8")

        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "browser exec diagnostic conflicts",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )

    def test_identity_failure_requires_scenario_bound_closed_diagnostic(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        run_dir = exp_dir / "run"
        run_dir.mkdir(parents=True)
        failure = {
            "schema": protocol.PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "native_measurement",
            "operation": "preview_browser_identity",
            "browser_identity_substage": "live_running_image_identity",
        }
        failure_path = exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH
        failure_path.write_text(json.dumps(failure), encoding="utf-8")
        diagnostic_path = (
            exp_dir / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
        )
        (
            exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        ).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": "preview_browser_identity",
                }
            ),
            encoding="utf-8",
        )
        (
            exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
        ).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
                    "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                    "probe": "chromium-version-immediate-exit",
                    "outer": "passed",
                    "nested": "passed",
                    "node_attached": "not-run",
                    "node_detached": "not-run",
                    "node_failure_kind": "not-run",
                    "prelaunched_cdp": "failed",
                }
            ),
            encoding="utf-8",
        )

        def write_diagnostic(**overrides: object) -> None:
            diagnostic = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": failure["browser_identity_substage"],
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": hashlib.sha256(failure_path.read_bytes()).hexdigest(),
                },
                **overrides,
            }
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")

        write_diagnostic()
        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )
        failure_path.write_text(
            "{\"schema\":\"cvm.provider-free-scenario-failure/1\","
            "\"scenario_identity\":\"issue15.provider-free.runtime-authority/1\","
            "\"stage\":\"native_measurement\","
            "\"operation\":\"preview_browser_identity\","
            "\"browser_identity_substage\":\"connected_cdp_browser_version_identity\","
            "\"browser_identity_substage\":\"live_running_image_identity\"}",
            encoding="utf-8",
        )
        write_diagnostic()
        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "scenario failure receipt",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )
        failure_path.write_text(json.dumps(failure), encoding="utf-8")
        for mutation in (
            "missing",
            "unknown",
            "duplicate",
            "inconsistent",
            "reordered",
            "unbound",
            "open",
        ):
            with self.subTest(mutation=mutation):
                failure["browser_identity_substage"] = (
                    "live_running_image_identity"
                )
                failure_path.write_text(json.dumps(failure), encoding="utf-8")
                if mutation == "missing":
                    diagnostic_path.unlink(missing_ok=True)
                elif mutation == "unknown":
                    write_diagnostic(substage="raw-linux-errno")
                elif mutation == "duplicate":
                    diagnostic_path.write_text(
                        "{\"schema\":\"cvm.provider-free-browser-identity-diagnostic/4\","
                        "\"operation\":\"preview_browser_identity\","
                        "\"substage\":\"live_running_image_identity\","
                        "\"substage\":\"connected_cdp_browser_version_identity\","
                        "\"scenario_failure\":{"
                        "\"path\":\"run/scenario-failure.json\","
                        f"\"sha256\":\"{hashlib.sha256(failure_path.read_bytes()).hexdigest()}\"}}",
                        encoding="utf-8",
                    )
                elif mutation == "inconsistent":
                    write_diagnostic(substage="connected_cdp_browser_version_identity")
                elif mutation == "reordered":
                    failure["browser_identity_substage"] = (
                        "connected_cdp_browser_version_identity"
                    )
                    failure_path.write_text(json.dumps(failure), encoding="utf-8")
                    write_diagnostic(substage="live_running_image_identity")
                elif mutation == "unbound":
                    write_diagnostic(
                        scenario_failure={
                            "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                            "sha256": "0" * 64,
                        }
                    )
                else:
                    write_diagnostic(endpoint="http://127.0.0.1:49152")
                with self.assertRaisesRegex(
                    provider_free_runner.ProviderFreeError,
                    "browser identity diagnostic",
                ):
                    provider_free_runner._validate_scenario_failure_evidence(
                        exp_dir,
                        "issue15-runtime-authority",
                    )

        failure["browser_identity_substage"] = (
            "private_snapshot_launch_image_identity"
        )
        failure["browser_identity_phase"] = "playwright_package_revision_identity"
        failure["browser_identity_check"] = "browser_manifest_entry"

        def write_check_diagnostic(
            *,
            write_failure: bool = True,
            **overrides: object,
        ) -> None:
            if write_failure:
                failure_path.write_text(json.dumps(failure), encoding="utf-8")
            diagnostic = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": "private_snapshot_launch_image_identity",
                "phase": failure.get("browser_identity_phase"),
                "check": failure.get("browser_identity_check"),
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": hashlib.sha256(failure_path.read_bytes()).hexdigest(),
                },
                **overrides,
            }
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")

        write_check_diagnostic()
        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )
        for mutation in (
            "missing",
            "duplicate",
            "unknown",
            "reordered",
            "other-phase",
            "unbound",
            "recomputed",
        ):
            with self.subTest(package_revision_check=mutation):
                failure["browser_identity_phase"] = (
                    "playwright_package_revision_identity"
                )
                failure["browser_identity_check"] = "browser_manifest_entry"
                write_check_diagnostic()
                if mutation == "missing":
                    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
                    diagnostic.pop("check")
                    diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")
                elif mutation == "duplicate":
                    digest = hashlib.sha256(failure_path.read_bytes()).hexdigest()
                    diagnostic_path.write_text(
                        "{\"schema\":\"cvm.provider-free-browser-identity-diagnostic/4\","
                        "\"operation\":\"preview_browser_identity\","
                        "\"substage\":\"private_snapshot_launch_image_identity\","
                        "\"phase\":\"playwright_package_revision_identity\","
                        "\"check\":\"python_distribution_metadata\","
                        "\"check\":\"browser_manifest_entry\","
                        "\"scenario_failure\":{"
                        "\"path\":\"run/scenario-failure.json\","
                        f"\"sha256\":\"{digest}\"}}",
                        encoding="utf-8",
                    )
                elif mutation == "unknown":
                    write_check_diagnostic(check="raw-package-error")
                elif mutation == "reordered":
                    write_check_diagnostic(check="python_distribution_metadata")
                elif mutation == "other-phase":
                    failure["browser_identity_phase"] = "private_launch_image_identity"
                    failure.pop("browser_identity_check")
                    write_check_diagnostic(check="browser_manifest_entry")
                elif mutation == "unbound":
                    write_check_diagnostic(
                        scenario_failure={
                            "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                            "sha256": "0" * 64,
                        }
                    )
                else:
                    failure_path.write_text(
                        "{\"schema\":\"cvm.provider-free-scenario-failure/1\","
                        "\"scenario_identity\":\"issue15.provider-free.runtime-authority/1\","
                        "\"stage\":\"native_measurement\","
                        "\"operation\":\"preview_browser_identity\","
                        "\"browser_identity_substage\":\"private_snapshot_launch_image_identity\","
                        "\"browser_identity_phase\":\"playwright_package_revision_identity\","
                        "\"browser_identity_check\":\"python_distribution_metadata\","
                        "\"browser_identity_check\":\"browser_manifest_entry\"}",
                        encoding="utf-8",
                    )
                    write_check_diagnostic(write_failure=False)
                with self.assertRaisesRegex(
                    provider_free_runner.ProviderFreeError,
                    "(browser identity diagnostic|scenario failure receipt)",
                ):
                    provider_free_runner._validate_scenario_failure_evidence(
                        exp_dir,
                        "issue15-runtime-authority",
                    )
        for check in sorted(
            protocol.PROVIDER_FREE_PRIVATE_VERSION_EXECUTION_CHECKS
        ):
            with self.subTest(private_version_execution_check=check):
                failure["browser_identity_phase"] = (
                    "private_launch_version_execution"
                )
                failure["browser_identity_check"] = check
                write_check_diagnostic()
                provider_free_runner._validate_scenario_failure_evidence(
                    exp_dir,
                    "issue15-runtime-authority",
                )
        failure["browser_identity_phase"] = "private_launch_version_execution"
        failure["browser_identity_check"] = "browser_manifest_entry"
        write_check_diagnostic()
        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "scenario failure receipt",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )
        write_diagnostic()

    def test_failure_requires_matching_closed_preview_public_wrapper(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True)
        operation = "preview_public_command_evidence_publication"
        (exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
                    "scenario_identity": (
                        "issue15.provider-free.runtime-authority/1"
                    ),
                    "stage": "native_measurement",
                    "operation": operation,
                }
            ),
            encoding="utf-8",
        )
        wrapper_path = exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        wrapper_path.write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": operation,
                }
            ),
            encoding="utf-8",
        )

        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )
        wrapper_path.write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": "preview_public_spawn",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "preview public wrapper conflicts",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )
        wrapper_path.write_text(
            "{\"schema\":\"cvm.provider-free-preview-public-wrapper/1\","
            "\"operation\":\"passed\","
            f"\"operation\":{json.dumps(operation)}}}",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "preview public wrapper",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )

    def test_private_snapshot_failure_requires_phase_bound_diagnostic(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        run_dir = exp_dir / "run"
        run_dir.mkdir(parents=True)
        failure = {
            "schema": protocol.PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "native_measurement",
            "operation": "preview_browser_identity",
            "browser_identity_substage": (
                "private_snapshot_launch_image_identity"
            ),
            "browser_identity_phase": "private_launch_image_identity",
        }
        failure_path = exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH
        failure_path.write_text(json.dumps(failure), encoding="utf-8")
        (
            exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        ).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": "preview_browser_identity",
                }
            ),
            encoding="utf-8",
        )
        (
            exp_dir / protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
        ).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
                    "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                    "probe": "chromium-version-immediate-exit",
                    "outer": "passed",
                    "nested": "passed",
                    "node_attached": "not-run",
                    "node_detached": "not-run",
                    "node_failure_kind": "not-run",
                    "prelaunched_cdp": "failed",
                }
            ),
            encoding="utf-8",
        )
        (
            exp_dir / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
        ).write_text(
            json.dumps(
                {
                    "schema": (
                        protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
                    ),
                    "operation": "preview_browser_identity",
                    "substage": "private_snapshot_launch_image_identity",
                    "phase": "private_launch_image_identity",
                    "scenario_failure": {
                        "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                        "sha256": hashlib.sha256(
                            failure_path.read_bytes()
                        ).hexdigest(),
                    },
                }
            ),
            encoding="utf-8",
        )

        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )

        diagnostic_path = (
            exp_dir / protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
        )

        def write_phase_diagnostic(**overrides: object) -> None:
            diagnostic = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": "private_snapshot_launch_image_identity",
                "phase": failure["browser_identity_phase"],
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": hashlib.sha256(
                        failure_path.read_bytes()
                    ).hexdigest(),
                },
                **overrides,
            }
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")

        for mutation in (
            "missing",
            "duplicate",
            "unknown",
            "reordered",
            "unbound",
            "recomputed",
        ):
            with self.subTest(mutation=mutation):
                failure["browser_identity_phase"] = (
                    "private_launch_image_identity"
                )
                failure_path.write_text(json.dumps(failure), encoding="utf-8")
                write_phase_diagnostic()
                if mutation == "missing":
                    diagnostic = json.loads(
                        diagnostic_path.read_text(encoding="utf-8")
                    )
                    diagnostic.pop("phase")
                    diagnostic_path.write_text(
                        json.dumps(diagnostic), encoding="utf-8"
                    )
                elif mutation == "duplicate":
                    diagnostic_path.write_text(
                        "{\"schema\":\"cvm.provider-free-browser-identity-diagnostic/4\","
                        "\"operation\":\"preview_browser_identity\","
                        "\"substage\":\"private_snapshot_launch_image_identity\","
                        "\"phase\":\"source_executable_identity\","
                        "\"phase\":\"private_launch_image_identity\","
                        "\"scenario_failure\":{"
                        "\"path\":\"run/scenario-failure.json\","
                        f"\"sha256\":\"{hashlib.sha256(failure_path.read_bytes()).hexdigest()}\"}}",
                        encoding="utf-8",
                    )
                elif mutation == "unknown":
                    write_phase_diagnostic(phase="raw-copy-error")
                elif mutation == "reordered":
                    write_phase_diagnostic(
                        phase="private_tree_materialization"
                    )
                elif mutation == "unbound":
                    write_phase_diagnostic(
                        scenario_failure={
                            "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                            "sha256": "0" * 64,
                        }
                    )
                else:
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
                    write_phase_diagnostic()
                with self.assertRaisesRegex(
                    provider_free_runner.ProviderFreeError,
                    "(browser identity diagnostic|scenario failure receipt)",
                ):
                    provider_free_runner._validate_scenario_failure_evidence(
                        exp_dir,
                        "issue15-runtime-authority",
                    )

    def test_wrapper_publication_root_requires_absent_wrapper_receipt(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True)
        (exp_dir / protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH).write_text(
            json.dumps(
                {
                    "schema": protocol.PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
                    "scenario_identity": (
                        "issue15.provider-free.runtime-authority/1"
                    ),
                    "stage": "native_measurement",
                    "operation": "preview_public_wrapper_evidence_publication",
                }
            ),
            encoding="utf-8",
        )

        provider_free_runner._validate_scenario_failure_evidence(
            exp_dir,
            "issue15-runtime-authority",
        )
        (
            exp_dir / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        ).write_text(
            '{"schema":"partial"',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            provider_free_runner.ProviderFreeError,
            "preview public wrapper must be absent",
        ):
            provider_free_runner._validate_scenario_failure_evidence(
                exp_dir,
                "issue15-runtime-authority",
            )

    def test_success_runs_closed_scenario_in_network_isolated_bounded_sandbox(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(argv, 0, stdout="bubblewrap 1.2.3\n")
            if not list(argv) or list(argv)[0] != os.fspath(self.bwrap):
                return subprocess.CompletedProcess(argv, 0, stdout="")
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            attested_target = (
                f"{protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested"
            )
            mount_index = next(
                index
                for index, value in enumerate(argv[:-2])
                if value == "--ro-bind" and argv[index + 2] == attested_target
            )
            host_stage = Path(argv[mount_index + 1])
            self.assertTrue(host_stage.is_dir())
            self.assertTrue(
                (
                    host_stage
                    / "chrome-headless-shell-linux64/chrome-headless-shell"
                ).is_file()
            )
            captured["host_stage"] = host_stage
            self.write_success_evidence()
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(provider_free_runner.subprocess, "run", side_effect=fake_run),
            mock.patch.object(provider_free_runner.pilot_runner, "validate_workspace_delivery", return_value={"identity_sha256": "a" * 64}),
            mock.patch.object(provider_free_runner.pilot_runner, "publish_workspace_authority", side_effect=self.write_authority),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 0)
        argv = captured["argv"]
        self.assertIn("--unshare-net", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("issue15-runtime-authority", argv)
        self.assertTrue(callable(captured["kwargs"]["preexec_fn"]))
        self.assertEqual(captured["kwargs"]["timeout"], 1800)
        self.assertFalse(captured["host_stage"].parent.exists())

        exp_dir = self.repo / "outputs" / self.handle
        proof_path = exp_dir / "run" / "provider-free-execution.json"
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        self.assertEqual(proof["job"], self.handle)
        self.assertEqual(proof["requests"], {"model_gateway": 0, "provider": 0, "tap": 0})
        self.assertEqual(proof["sandbox"]["network"], "isolated-loopback")
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["final_status"], 0)
        retained_receipt = json.loads(
            (exp_dir / "run/deployed-source-authority.json").read_text(
                encoding="utf-8"
            )
        )
        deployment_authority.verify_materialized(
            exp_dir / "run/deployed-source",
            retained_receipt,
        )
        sandbox = json.loads(
            (exp_dir / "run/sandbox-enforcement.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sandbox["network"], "isolated-loopback")
        self.assertIn("--unshare-net", sandbox["argv"])
        proof_bytes = proof_path.read_bytes()
        self.assertIn(
            {
                "path": "run/provider-free-execution.json",
                "size_bytes": len(proof_bytes),
                "sha256": hashlib.sha256(proof_bytes).hexdigest(),
            },
            manifest["files"],
        )

    def test_deployed_authority_is_retained_and_manifest_bound_before_workload(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        observed_before_launch: dict[str, object] = {}

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            authority = exp_dir / "run/deployed-source-authority.json"
            retained = exp_dir / "run/deployed-source"
            observed_before_launch.update(
                authority_exists=authority.is_file(),
                retained_exists=retained.is_dir(),
            )
            self.write_success_evidence()
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
            mock.patch.object(
                provider_free_runner.pilot_runner,
                "validate_workspace_delivery",
                return_value={"identity_sha256": "a" * 64},
            ),
            mock.patch.object(
                provider_free_runner.pilot_runner,
                "publish_workspace_authority",
                side_effect=self.write_authority,
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            observed_before_launch,
            {"authority_exists": True, "retained_exists": True},
        )
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "run/deployed-source-authority.json",
            {entry["path"] for entry in manifest["files"]},
        )

    def test_deployment_retention_failure_is_terminal_before_workload_launch(self) -> None:
        workload_launches: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            if list(argv) and list(argv)[0] == os.fspath(self.bwrap):
                workload_launches.append(list(argv))
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
            mock.patch.object(
                provider_free_runner,
                "_retain_deployment_authority",
                side_effect=provider_free_runner.ProviderFreeError(
                    "deployed source authority retention failed"
                ),
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(
            status, provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS
        )
        self.assertEqual(workload_launches, [])
        exp_dir = self.repo / "outputs" / self.handle
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["final_status"],
            provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS,
        )
        self.assertFalse((exp_dir / "run/sandbox-enforcement.json").exists())

    def test_nonzero_scenario_manifest_binds_retained_authority_and_failure(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            (exp_dir / "run").mkdir(parents=True, exist_ok=True)
            (exp_dir / "run/scenario-failure.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm.provider-free-scenario-failure/1",
                        "scenario_identity": (
                            "issue15.provider-free.runtime-authority/1"
                        ),
                        "stage": "viewer_fallback",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 1)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 1)
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("run/scenario-failure.json", paths)
        self.assertIn("run/deployed-source-authority.json", paths)
        self.assertFalse(
            (
                self.browser_cache
                / ".cvm-provider-free-browser-stages"
            ).exists()
        )

    def test_workload_launch_exception_cleans_host_browser_stage(self) -> None:
        def fail_workload(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            if list(argv) and list(argv)[0] == os.fspath(self.bwrap):
                raise OSError("injected launch failure")
            return subprocess.CompletedProcess(argv, 0, stdout="")

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fail_workload,
            ),
            self.assertRaises(OSError),
        ):
            provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertFalse(
            (
                self.browser_cache
                / ".cvm-provider-free-browser-stages"
            ).exists()
        )

    def test_unknown_scenario_is_rejected_before_sandbox_start(self) -> None:
        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(provider_free_runner.subprocess, "run") as run,
        ):
            status = provider_free_runner.main(
                ["run", "../../bin/sh", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        run.assert_not_called()
        self.assertFalse((self.repo / "outputs" / self.handle).exists())

    def test_path_shadow_bwrap_is_rejected_before_sandbox_start(self) -> None:
        shadow = self.repo / "bin/bwrap"
        shadow.parent.mkdir(parents=True)
        shadow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shadow.chmod(0o755)
        with (
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(shadow),
            ),
            mock.patch.object(provider_free_runner.subprocess, "run") as run,
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        run.assert_not_called()

    def test_wrong_attested_chromium_revision_is_rejected_before_output(self) -> None:
        self.browser.write_bytes(b"tampered-browser")
        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        self.assertFalse((self.repo / "outputs" / self.handle).exists())

    def test_output_symlink_escape_is_rejected_before_any_process_or_write(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        outputs = self.repo / "outputs"
        for mutation in ("group", "exp"):
            with self.subTest(mutation=mutation):
                group_path = outputs / self.group
                try:
                    if mutation == "group":
                        group_path.symlink_to(outside, target_is_directory=True)
                    else:
                        group_path.mkdir()
                        (group_path / self.exp).symlink_to(
                            outside,
                            target_is_directory=True,
                        )
                    with (
                        mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
                        mock.patch.object(
                            provider_free_runner.shutil,
                            "which",
                            return_value=os.fspath(self.bwrap),
                        ),
                        mock.patch.object(
                            provider_free_runner.subprocess,
                            "run",
                        ) as run,
                    ):
                        run.side_effect = (
                            lambda argv, **kwargs: subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout=(
                                    "bubblewrap 1.2.3\n"
                                    if list(argv)
                                    == [os.fspath(self.bwrap), "--version"]
                                    else ""
                                ),
                            )
                        )
                        status = provider_free_runner.main(
                            ["run", "issue15-runtime-authority", self.group, self.exp],
                            environ=self.environment,
                        )

                    self.assertEqual(status, 2)
                    run.assert_not_called()
                    self.assertEqual([], list(outside.iterdir()))
                finally:
                    if group_path.is_symlink():
                        group_path.unlink()
                    elif group_path.exists():
                        for child in group_path.iterdir():
                            child.unlink()
                        group_path.rmdir()

    def test_output_path_is_revalidated_after_sandbox_returns(self) -> None:
        outside = Path(self.temporary.name) / "outside-race"
        outside.mkdir()
        exp_dir = self.repo / "outputs" / self.handle

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            if not list(argv) or list(argv)[0] != os.fspath(self.bwrap):
                return subprocess.CompletedProcess(argv, 0)
            shutil.rmtree(exp_dir)
            exp_dir.symlink_to(outside, target_is_directory=True)
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        self.assertEqual([], list(outside.iterdir()))
        self.assertTrue(exp_dir.is_symlink())
        exp_dir.unlink()
        exp_dir.parent.rmdir()

    def test_precreated_exact_exp_and_poisoned_children_are_rejected(self) -> None:
        real_run = subprocess.run
        for mutation in ("empty", ".git", "run", ".gitignore"):
            with self.subTest(mutation=mutation):
                exp_dir = self.repo / "outputs" / self.handle
                exp_dir.mkdir(parents=True)
                outside = Path(self.temporary.name) / f"outside-{mutation.strip('.')}"
                before: bytes | list[Path] | None = None
                if mutation == ".git":
                    outside.mkdir()
                    real_run(
                        ["git", "init", "--bare", os.fspath(outside)],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    before = (outside / "config").read_bytes()
                    (exp_dir / mutation).symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                elif mutation == "run":
                    outside.mkdir()
                    before = list(outside.iterdir())
                    (exp_dir / mutation).symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                elif mutation == ".gitignore":
                    outside.write_text("sentinel\n", encoding="utf-8")
                    before = outside.read_bytes()
                    (exp_dir / mutation).symlink_to(outside)

                def fake_run(argv, **kwargs):
                    if list(argv) == [os.fspath(self.bwrap), "--version"]:
                        return subprocess.CompletedProcess(
                            argv, 0, stdout="bubblewrap 1.2.3\n"
                        )
                    if list(argv) and list(argv)[0] == "git":
                        return real_run(argv, **kwargs)
                    return subprocess.CompletedProcess(argv, 0, stdout="")

                try:
                    with (
                        mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
                        mock.patch.object(
                            provider_free_runner.shutil,
                            "which",
                            return_value=os.fspath(self.bwrap),
                        ),
                        mock.patch.object(
                            provider_free_runner.subprocess,
                            "run",
                            side_effect=fake_run,
                        ) as run,
                    ):
                        status = provider_free_runner.main(
                            ["run", "issue15-runtime-authority", self.group, self.exp],
                            environ=self.environment,
                        )

                    if mutation == ".git":
                        self.assertEqual(before, (outside / "config").read_bytes())
                    elif mutation == "run":
                        self.assertEqual(before, list(outside.iterdir()))
                    elif mutation == ".gitignore":
                        self.assertEqual(before, outside.read_bytes())
                    run.assert_not_called()
                    self.assertEqual(2, status)
                finally:
                    shutil.rmtree(exp_dir)
                    exp_dir.parent.rmdir()

    def test_exact_exp_creation_race_during_runtime_probe_is_rejected(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        outside = Path(self.temporary.name) / "outside-probe-race"
        outside.write_text("sentinel\n", encoding="utf-8")

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                exp_dir.mkdir(parents=True)
                (exp_dir / ".gitignore").symlink_to(outside)
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            raise AssertionError(f"unexpected subprocess after creation race: {argv}")

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ) as run,
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(2, status)
        self.assertEqual(1, run.call_count)
        self.assertEqual("sentinel\n", outside.read_text(encoding="utf-8"))
        shutil.rmtree(exp_dir)
        exp_dir.parent.rmdir()

    def test_exit_zero_without_runtime_authority_receipt_fails_terminalization(self) -> None:
        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(argv, 0, stdout="bubblewrap 1.2.3\n")
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
            mock.patch.object(provider_free_runner.pilot_runner, "validate_workspace_delivery", return_value={"identity_sha256": "a" * 64}),
            mock.patch.object(provider_free_runner.pilot_runner, "publish_workspace_authority", return_value={"mode": "live"}),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS)
        manifest = json.loads(
            (self.repo / "outputs" / self.handle / "artifact_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["final_status"],
            provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS,
        )

    def test_terminal_manifest_rejects_symlinks_and_special_files(self) -> None:
        for mutation in ("symlink", "fifo"):
            with self.subTest(mutation=mutation):
                exp_dir = self.repo / "outputs" / self.handle
                exp_dir.mkdir(parents=True, exist_ok=True)
                unsafe = exp_dir / f"unsafe-{mutation}"
                if mutation == "symlink":
                    outside = self.repo / "outside-secret"
                    outside.write_text("secret\n", encoding="utf-8")
                    unsafe.symlink_to(outside)
                else:
                    os.mkfifo(unsafe)
                with (
                    mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
                    self.assertRaisesRegex(
                        provider_free_runner.ProviderFreeError,
                        "symlink|special",
                    ),
                ):
                    provider_free_runner._publish_terminal_manifest(
                        exp_dir,
                        workload_status=0,
                        final_status=0,
                    )
                unsafe.unlink()


if __name__ == "__main__":
    unittest.main()
