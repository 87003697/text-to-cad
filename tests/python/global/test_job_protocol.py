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
