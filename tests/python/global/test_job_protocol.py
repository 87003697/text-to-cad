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
            "schema": "cvm.provider-free-browser-exec-diagnostic/4",
            "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            "probe": "chromium-version-immediate-exit",
            "outer": "passed",
            "nested": "passed",
            "node_attached": "passed",
            "node_detached": "passed",
            "node_failure_kind": "not-run",
            "playwright": "failed",
        }

        self.assertTrue(
            protocol.provider_free_browser_exec_diagnostic_allowed(receipt)
        )
        attached_failure = {
            **receipt,
            "node_attached": "failed",
            "node_detached": "not-run",
            "node_failure_kind": "spawn-event",
            "playwright": "not-run",
        }
        self.assertTrue(
            protocol.provider_free_browser_exec_diagnostic_matches_operation(
                attached_failure,
                "preview_browser_node_attached_spawn_event",
            )
        )
        detached_failure = {
            **receipt,
            "node_detached": "failed",
            "node_failure_kind": "timeout",
            "playwright": "not-run",
        }
        self.assertTrue(
            protocol.provider_free_browser_exec_diagnostic_matches_operation(
                detached_failure,
                "preview_browser_node_detached_timeout",
            )
        )
        self.assertFalse(
            protocol.provider_free_browser_exec_diagnostic_matches_operation(
                detached_failure,
                "preview_browser_nested_exec_probe",
            )
        )
        for mutation in (
            {**receipt, "stdout": "sensitive raw version"},
            {**receipt, "outer": "unknown"},
            {**receipt, "outer": "failed", "nested": "passed"},
            {**receipt, "nested": "failed", "playwright": "passed"},
            {**receipt, "node_attached": "failed", "playwright": "passed"},
            {**receipt, "node_detached": "failed", "playwright": "passed"},
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
