from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.pilot.cvm_job import protocol
from tests.python.support.tmp_root import temporary_directory


def pilot_record(handle: str, state: str = "running") -> dict[str, object]:
    group, exp = handle.split("/")
    now = protocol.utc_now()
    return {
        "schema_version": 1,
        "kind": "pilot",
        "job": handle,
        "group": group,
        "exp": exp,
        "state": state,
        "updated_at": now,
        "heartbeat_at": now,
    }


class JobProtocolTests(unittest.TestCase):
    def test_version_execution_diagnostic_requires_one_exact_check(self) -> None:
        self.assertEqual(
            "cvm.provider-free-browser-identity-diagnostic/4",
            protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
        )
        checks = (
            "private_version_probe_spawn",
            "private_version_probe_timeout",
            "sealed_memfd_creation_policy",
        )
        for check in checks:
            receipt = {
                "schema": "cvm.provider-free-browser-identity-diagnostic/4",
                "operation": "preview_browser_identity",
                "substage": "private_snapshot_launch_image_identity",
                "phase": "private_launch_version_execution",
                "check": check,
                "scenario_failure": {
                    "path": "run/scenario-failure.json",
                    "sha256": "9" * 64,
                },
            }
            with self.subTest(check=check):
                self.assertTrue(
                    protocol.provider_free_browser_identity_diagnostic_allowed(
                        receipt,
                        expected_failure_sha256="9" * 64,
                        expected_substage="private_snapshot_launch_image_identity",
                        expected_phase="private_launch_version_execution",
                        expected_check=check,
                    )
                )
                for mutation in (
                    {key: value for key, value in receipt.items() if key != "check"},
                    {**receipt, "check": "raw-exec-error"},
                    {**receipt, "schema": "cvm.provider-free-browser-identity-diagnostic/3"},
                ):
                    self.assertFalse(
                        protocol.provider_free_browser_identity_diagnostic_allowed(
                            mutation,
                            expected_failure_sha256="9" * 64,
                            expected_substage="private_snapshot_launch_image_identity",
                            expected_phase="private_launch_version_execution",
                            expected_check=check,
                        )
                    )

    def test_browser_identity_diagnostic_is_closed_and_failure_bound(self) -> None:
        receipt = {
            "schema": "cvm.provider-free-browser-identity-diagnostic/3",
            "operation": "preview_browser_identity",
            "substage": "connected_cdp_browser_version_identity",
            "scenario_failure": {
                "path": "run/scenario-failure.json",
                "sha256": "a" * 64,
            },
        }

        self.assertTrue(
            protocol.provider_free_browser_identity_diagnostic_allowed(
                receipt,
                expected_failure_sha256="a" * 64,
                expected_substage="connected_cdp_browser_version_identity",
            )
        )
        for mutation in (
            {**receipt, "pid": 1234},
            {**receipt, "operation": "preview_browser_runtime_evidence"},
            {**receipt, "substage": "raw-linux-error"},
            {
                **receipt,
                "scenario_failure": {
                    **receipt["scenario_failure"],
                    "path": "../../scenario-failure.json",
                },
            },
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    protocol.provider_free_browser_identity_diagnostic_allowed(
                        mutation,
                        expected_failure_sha256="a" * 64,
                        expected_substage=(
                            "connected_cdp_browser_version_identity"
                        ),
                    )
                )
        self.assertFalse(
            protocol.provider_free_browser_identity_diagnostic_allowed(
                receipt,
                expected_failure_sha256="b" * 64,
                expected_substage="connected_cdp_browser_version_identity",
            )
        )
        self.assertFalse(
            protocol.provider_free_browser_identity_diagnostic_allowed(
                receipt,
                expected_failure_sha256="a" * 64,
                expected_substage="live_running_image_identity",
            )
        )

    def test_browser_identity_diagnostic_accepts_every_versioned_substage(self) -> None:
        for substage in sorted(protocol.PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES):
            with self.subTest(substage=substage):
                phase = (
                    "private_launch_image_identity"
                    if substage == "private_snapshot_launch_image_identity"
                    else None
                )
                diagnostic = {
                    "schema": (
                        protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
                    ),
                    "operation": "preview_browser_identity",
                    "substage": substage,
                    "scenario_failure": {
                        "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                        "sha256": "c" * 64,
                    },
                }
                if phase is not None:
                    diagnostic["phase"] = phase
                self.assertTrue(
                    protocol.provider_free_browser_identity_diagnostic_allowed(
                        diagnostic,
                        expected_failure_sha256="c" * 64,
                        expected_substage=substage,
                        expected_phase=phase,
                    )
                )
    def test_private_snapshot_diagnostic_requires_one_exact_phase(self) -> None:
        for phase in sorted(protocol.PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES):
            check = (
                "python_distribution_metadata"
                if phase == "playwright_package_revision_identity"
                else None
            )
            receipt = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": "private_snapshot_launch_image_identity",
                "phase": phase,
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": "d" * 64,
                },
            }
            if check is not None:
                receipt["check"] = check
            with self.subTest(phase=phase):
                self.assertTrue(
                    protocol.provider_free_browser_identity_diagnostic_allowed(
                        receipt,
                        expected_failure_sha256="d" * 64,
                        expected_substage="private_snapshot_launch_image_identity",
                        expected_phase=phase,
                        expected_check=check,
                    )
                )
                for mutation in (
                    {key: value for key, value in receipt.items() if key != "phase"},
                    {**receipt, "phase": "raw-copy-error"},
                    {
                        **receipt,
                        "phase": next(
                            value
                            for value in sorted(
                                protocol.PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                            )
                            if value != phase
                        ),
                    },
                    {**receipt, "detail": "permission denied"},
                ):
                    self.assertFalse(
                        protocol.provider_free_browser_identity_diagnostic_allowed(
                            mutation,
                            expected_failure_sha256="d" * 64,
                            expected_substage="private_snapshot_launch_image_identity",
                            expected_phase=phase,
                            expected_check=check,
                        )
                    )
        non_private = {
            "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "operation": "preview_browser_identity",
            "substage": "live_running_image_identity",
            "phase": "source_executable_identity",
            "scenario_failure": {
                "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                "sha256": "e" * 64,
            },
        }
        self.assertFalse(
            protocol.provider_free_browser_identity_diagnostic_allowed(
                non_private,
                expected_failure_sha256="e" * 64,
                expected_substage="live_running_image_identity",
            )
        )

    def test_playwright_package_diagnostic_requires_one_exact_check(self) -> None:
        checks = tuple(sorted(protocol.PROVIDER_FREE_PLAYWRIGHT_PACKAGE_REVISION_CHECKS))
        for check in checks:
            receipt = {
                "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                "operation": "preview_browser_identity",
                "substage": "private_snapshot_launch_image_identity",
                "phase": "playwright_package_revision_identity",
                "check": check,
                "scenario_failure": {
                    "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                    "sha256": "f" * 64,
                },
            }
            with self.subTest(check=check):
                self.assertTrue(
                    protocol.provider_free_browser_identity_diagnostic_allowed(
                        receipt,
                        expected_failure_sha256="f" * 64,
                        expected_substage="private_snapshot_launch_image_identity",
                        expected_phase="playwright_package_revision_identity",
                        expected_check=check,
                    )
                )
                for mutation in (
                    {key: value for key, value in receipt.items() if key != "check"},
                    {**receipt, "check": "raw-package-error"},
                    {**receipt, "check": next(value for value in checks if value != check)},
                    {**receipt, "detail": "package path"},
                ):
                    self.assertFalse(
                        protocol.provider_free_browser_identity_diagnostic_allowed(
                            mutation,
                            expected_failure_sha256="f" * 64,
                            expected_substage="private_snapshot_launch_image_identity",
                            expected_phase="playwright_package_revision_identity",
                            expected_check=check,
                        )
                    )
        self.assertFalse(
            protocol.provider_free_browser_identity_diagnostic_allowed(
                {
                    "schema": protocol.PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
                    "operation": "preview_browser_identity",
                    "substage": "private_snapshot_launch_image_identity",
                    "phase": "private_launch_image_identity",
                    "check": "browser_manifest_entry",
                    "scenario_failure": {
                        "path": protocol.PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                        "sha256": "f" * 64,
                    },
                },
                expected_failure_sha256="f" * 64,
                expected_substage="private_snapshot_launch_image_identity",
                expected_phase="private_launch_image_identity",
            )
        )

    def test_prelaunched_cdp_runtime_receipt_requires_frozen_exact_identities(self) -> None:
        receipt = {
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
                "sha256": "c" * 64,
            },
            "result": "passed",
        }
        self.assertTrue(
            protocol.provider_free_browser_runtime_allowed(
                receipt,
                expected_browser_sha256="c" * 64,
            )
        )
        for mutation, expected in (
            (
                {**receipt, "adapter_profile": {**receipt["adapter_profile"], "sha256": "1" * 64}},
                "c" * 64,
            ),
            (
                {
                    **receipt,
                    "browser_identity": {
                        **receipt["browser_identity"],
                        "sha256": "2" * 64,
                    },
                },
                "c" * 64,
            ),
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    protocol.provider_free_browser_runtime_allowed(
                        mutation,
                        expected_browser_sha256=expected,
                    )
                )

    def test_prelaunched_cdp_runtime_receipt_is_closed(self) -> None:
        receipt = {
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
                "sha256": "2" * 64,
            },
            "result": "passed",
        }
        self.assertTrue(
            protocol.provider_free_browser_runtime_allowed(
                receipt,
                expected_browser_sha256="2" * 64,
            )
        )
        for mutation in (
            {**receipt, "endpoint": "http://127.0.0.1:49152"},
            {**receipt, "pid": 1234},
            {**receipt, "argv": ["--remote-debugging-port=49152"]},
            {**receipt, "stderr": "sensitive"},
            {
                **receipt,
                "adapter_profile": {**receipt["adapter_profile"], "name": "stale/1"},
            },
            {
                **receipt,
                "browser_identity": {**receipt["browser_identity"], "revision": "1222"},
            },
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    protocol.provider_free_browser_runtime_allowed(
                        mutation,
                        expected_browser_sha256="2" * 64,
                    )
                )

    def test_preview_public_wrapper_receipt_is_closed_and_operation_bound(self) -> None:
        for operation in (
            "passed",
            *sorted(protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS),
        ):
            with self.subTest(operation=operation):
                receipt = {
                    "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                    "operation": operation,
                }
                self.assertTrue(
                    protocol.provider_free_preview_public_wrapper_allowed(receipt)
                )
                if operation != "passed":
                    self.assertTrue(
                        protocol.provider_free_preview_public_wrapper_matches_operation(
                            receipt,
                            operation,
                        )
                    )
        for receipt in (
            {
                "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                "operation": "voxblame_preview",
            },
            {
                "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                "operation": "preview_public_wrapper_evidence_publication",
            },
            {
                "schema": protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                "operation": "preview_public_spawn",
                "stderr": "sensitive",
            },
            {
                "schema": "cvm.provider-free-preview-public-wrapper/0",
                "operation": "preview_public_timeout",
            },
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(
                    protocol.provider_free_preview_public_wrapper_allowed(receipt)
                )

    def test_browser_exec_diagnostic_receipt_is_closed(self) -> None:
        receipt = {
            "schema": "cvm.provider-free-browser-exec-diagnostic/5",
            "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            "probe": "chromium-version-immediate-exit",
            "outer": "passed",
            "nested": "passed",
            "node_attached": "not-run",
            "node_detached": "not-run",
            "node_failure_kind": "not-run",
            "prelaunched_cdp": "failed",
        }

        self.assertTrue(
            protocol.provider_free_browser_exec_diagnostic_allowed(receipt)
        )
        self.assertFalse(
            protocol.provider_free_browser_exec_diagnostic_matches_operation(
                receipt,
                "preview_browser_nested_exec_probe",
            )
        )
        for mutation in (
            {**receipt, "stdout": "sensitive raw version"},
            {**receipt, "outer": "unknown"},
            {**receipt, "outer": "failed", "nested": "passed"},
            {**receipt, "nested": "failed", "prelaunched_cdp": "passed"},
            {**receipt, "node_attached": "failed", "prelaunched_cdp": "passed"},
            {**receipt, "node_detached": "failed", "prelaunched_cdp": "passed"},
            {**receipt, "node_attached": "passed"},
            {**receipt, "node_detached": "passed"},
            {**receipt, "node_failure_kind": "raw-errno"},
            {**receipt, "executable": "/tmp/other-browser"},
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    protocol.provider_free_browser_exec_diagnostic_allowed(
                        mutation
                    )
                )

    def test_preview_sandbox_receipt_is_closed_and_experiment_bound(self) -> None:
        group = "20260812-180000-preview"
        exp = "20260812-100000-issue15-runtime-authority"
        receipt = {
            "schema": protocol.PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
            "argv": protocol.provider_free_preview_sandbox_argv(group, exp),
            "capabilities": "drop-all",
            "mount_namespace": "inherit-outer",
        }

        self.assertTrue(
            protocol.provider_free_preview_sandbox_receipt_allowed(
                receipt, group, exp
            )
        )
        cache = "/tmp/provider-free-playwright"
        argv = receipt["argv"]
        cache_bind = argv.index("--ro-bind")
        self.assertEqual(
            ["--ro-bind", cache, cache], argv[cache_bind : cache_bind + 3]
        )
        setenv = argv.index("--setenv")
        self.assertEqual(
            ["--setenv", "PLAYWRIGHT_BROWSERS_PATH", cache],
            argv[setenv : setenv + 3],
        )
        executable_setenv = argv.index("--setenv", setenv + 1)
        self.assertEqual(
            [
                "--setenv",
                "MESHSHOT_BROWSER_EXECUTABLE",
                protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            ],
            argv[executable_setenv : executable_setenv + 3],
        )
        root_setenv = argv.index("--setenv", executable_setenv + 1)
        self.assertEqual(
            [
                "--setenv",
                "MESHSHOT_EXECUTABLE_ROOT",
                "/meshshot-exec",
            ],
            argv[root_setenv : root_setenv + 3],
        )
        for field, value in (
            ("capabilities", "inherit"),
            ("mount_namespace", "host"),
            ("argv", receipt["argv"][:-1]),
        ):
            with self.subTest(field=field):
                candidate = {**receipt, field: value}
                self.assertFalse(
                    protocol.provider_free_preview_sandbox_receipt_allowed(
                        candidate, group, exp
                    )
                )
        self.assertFalse(
            protocol.provider_free_preview_sandbox_receipt_allowed(
                {**receipt, "extra": True}, group, exp
            )
        )

    def test_safe_and_unsafe_handles(self) -> None:
        self.assertEqual(protocol.parse_handle("group/exp")["kind"], "pilot")
        for handle in (
            "",
            "group",
            "batch/group",
            "../exp",
            "group/..",
            "a/b/c",
            "/exp",
        ):
            with self.subTest(handle=handle), self.assertRaises(protocol.ProtocolError):
                protocol.parse_handle(handle)

    def test_atomic_reader_never_observes_partial_json(self) -> None:
        with temporary_directory(prefix="cvm-protocol-") as root_text:
            root = Path(root_text)
            handle = "group/exp"
            protocol.publish_state(root, pilot_record(handle))
            errors: list[Exception] = []

            def reader() -> None:
                for _ in range(200):
                    try:
                        protocol.load_state(root, handle)
                    except Exception as error:  # pragma: no cover - failure capture
                        errors.append(error)

            thread = threading.Thread(target=reader)
            thread.start()
            for index in range(50):
                protocol.heartbeat(root, handle, pilot_pid=index)
            thread.join()
            self.assertEqual(errors, [])
            leftovers = list(protocol.state_path(root, handle).parent.glob(".*.json.*"))
            self.assertEqual(leftovers, [])

    def test_terminal_state_cannot_return_to_running(self) -> None:
        with temporary_directory(prefix="cvm-protocol-") as root_text:
            root = Path(root_text)
            handle = "group/exp"
            protocol.publish_state(root, pilot_record(handle, "failed"))
            with self.assertRaisesRegex(protocol.ProtocolError, "invalid transition"):
                protocol.transition(root, handle, "running")

    def test_terminal_transition_is_idempotent_and_does_not_rewrite_record(self) -> None:
        with temporary_directory(prefix="cvm-protocol-") as root_text:
            root = Path(root_text)
            handle = "group/exp"
            record = pilot_record(handle, "failed")
            record["failure_reason"] = "original"
            protocol.publish_state(root, record)
            path = protocol.state_path(root, handle)
            before = path.read_bytes()

            result = protocol.transition(
                root,
                handle,
                "failed",
                failure_reason="replacement",
                process_exit_code=99,
            )

            self.assertEqual(result["failure_reason"], "original")
            self.assertNotIn("process_exit_code", result)
            self.assertEqual(path.read_bytes(), before)

            replacement = dict(result)
            replacement["failure_reason"] = "direct replacement"
            with self.assertRaisesRegex(
                protocol.ProtocolError,
                "terminal job record is immutable",
            ):
                protocol.publish_state(root, replacement)
            self.assertEqual(path.read_bytes(), before)

    def test_updates_cannot_override_protocol_owned_fields(self) -> None:
        with temporary_directory(prefix="cvm-protocol-") as root_text:
            root = Path(root_text)
            handle = "group/exp"
            protocol.publish_state(root, pilot_record(handle))

            attempts = (
                lambda: protocol.heartbeat(root, handle, state="failed"),
                lambda: protocol.heartbeat(root, handle, heartbeat_at="never"),
                lambda: protocol.transition(root, handle, "failed", job="other/exp"),
                lambda: protocol.transition(root, handle, "failed", finished_at="never"),
            )
            for attempt in attempts:
                with self.subTest(attempt=attempt), self.assertRaisesRegex(
                    protocol.ProtocolError,
                    "reserved update fields",
                ):
                    attempt()

            self.assertEqual(protocol.load_state(root, handle)["state"], "running")

    def test_provider_free_request_authority_is_immutable_across_every_write_seam(self) -> None:
        with temporary_directory(prefix="cvm-protocol-") as root_text:
            root = Path(root_text)
            handle = "group/exp"
            record = pilot_record(handle)
            record.update(
                {
                    "job_kind": "provider-free",
                    "object": "issue15-runtime-authority",
                    "exp_dir": "outputs/group/exp",
                    "scenario": {
                        "name": "issue15-runtime-authority",
                        "identity": "issue15.provider-free.runtime-authority/1",
                    },
                    "execution_profile": {"id": "issue15.provider-free-bounded/1"},
                    "request_authority": {
                        "schema": "cvm.provider-free-request-authority/1",
                        "deployment_identity": "a" * 64,
                    },
                }
            )
            record["request_authority_sha256"] = protocol.request_authority_sha256(
                record
            )
            protocol.publish_state(root, record)

            for field, replacement in (
                ("job_kind", "pilot"),
                ("object", "other"),
                ("exp_dir", "outputs/group/other"),
                ("scenario", {"name": "other"}),
                ("execution_profile", {"id": "other"}),
                ("request_authority", {"deployment_identity": "b" * 64}),
                ("request_authority_sha256", "0" * 64),
            ):
                with self.subTest(field=field), self.assertRaisesRegex(
                    protocol.ProtocolError, "reserved update fields"
                ):
                    protocol.heartbeat(root, handle, **{field: replacement})

                mutated = dict(protocol.load_state(root, handle))
                mutated[field] = replacement
                with self.assertRaisesRegex(
                    protocol.ProtocolError, "request authority|immutable"
                ):
                    protocol.publish_state(root, mutated)

            mutated = dict(protocol.load_state(root, handle))
            mutated["scenario"] = {"name": "other", "identity": "other/1"}
            mutated["request_authority_sha256"] = protocol.request_authority_sha256(
                mutated
            )
            with self.assertRaisesRegex(protocol.ProtocolError, "immutable"):
                protocol.publish_state(root, mutated)

    def test_stale_is_derived_and_does_not_persist_failure(self) -> None:
        state = pilot_record("group/exp")
        state["heartbeat_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        ).isoformat()
        public = protocol.public_state(state, stale_after=60)
        self.assertEqual(public["health"], "stale")
        self.assertEqual(state["state"], "running")

if __name__ == "__main__":
    unittest.main()
