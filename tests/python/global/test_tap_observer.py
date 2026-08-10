from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from scripts.pilot.cvm_job import tap_observer
from tests.python.support.tmp_root import temporary_directory


def create_trace(path: Path, *, version: int = 4) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version={version}")
    connection.execute(
        "CREATE TABLE sessions ("
        "id TEXT, status TEXT, record_count INTEGER, started_at TEXT, "
        "updated_at TEXT, summary_json TEXT)"
    )
    connection.execute(
        "CREATE TABLE records ("
        "session_id TEXT, record_index INTEGER, turn INTEGER, "
        "timestamp TEXT, payload_json TEXT)"
    )
    connection.execute(
        "CREATE TABLE record_blobs ("
        "blob_id TEXT, session_id TEXT, record_index INTEGER, payload_json TEXT)"
    )
    connection.execute(
        "CREATE TABLE proxy_logs (session_id TEXT, timestamp TEXT, payload_json TEXT)"
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
        (
            "session-1",
            "active",
            0,
            "2026-08-04T00:00:00Z",
            "2026-08-04T00:00:00Z",
            json.dumps({"usage": {"input_tokens": 12, "output_tokens": 3}}),
        ),
    )
    connection.commit()
    return connection


def insert_record(connection: sqlite3.Connection, index: int, payload: object) -> None:
    connection.execute(
        "INSERT INTO records VALUES (?,?,?,?,?)",
        (
            "session-1",
            index,
            index,
            f"2026-08-04T00:00:0{index}Z",
            json.dumps(payload),
        ),
    )
    connection.execute("UPDATE sessions SET record_count=?, updated_at=?", (index, f"2026-08-04T00:00:0{index}Z"))
    connection.commit()


class TapObserverTests(unittest.TestCase):
    def test_missing_db_is_pending(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            result = tap_observer.observe(Path(root_text) / "missing.sqlite3")
        self.assertEqual(result, {"tap": {"availability": "pending"}})

    def test_active_summary_and_plain_response(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "trace.sqlite3"
            connection = create_trace(path)
            insert_record(
                connection,
                1,
                {
                    "response": {
                        "model": "gpt-5.6-sol",
                        "status_code": 200,
                        "duration_ms": 521,
                        "usage": {"input_tokens": 20, "output_tokens": 5},
                    }
                },
            )
            connection.close()
            result = tap_observer.observe(path)
        self.assertEqual(result["tap"]["availability"], "ready")
        self.assertEqual(result["tap"]["turn_count"], 1)
        self.assertEqual(result["tap"]["last_api"]["model"], "gpt-5.6-sol")
        self.assertEqual(result["tap"]["last_api"]["status"], 200)
        self.assertEqual(result["tap"]["last_usage"]["input_tokens"], 20)
        self.assertEqual(result["tap"]["last_usage"]["output_tokens"], 5)

    def test_last_usage_is_not_summed_across_records(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "trace.sqlite3"
            connection = create_trace(path)
            insert_record(
                connection,
                1,
                {"response": {"usage": {"input_tokens": 20, "output_tokens": 5}}},
            )
            insert_record(
                connection,
                2,
                {"response": {"usage": {"input_tokens": 7, "output_tokens": 2}}},
            )
            connection.close()
            result = tap_observer.observe(path)
        self.assertEqual(
            result["tap"]["last_usage"],
            {
                "input_tokens": 7,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_create_tokens": 0,
            },
        )

    def test_proxy_log_can_report_inflight_api_without_records(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "trace.sqlite3"
            connection = create_trace(path)
            connection.execute(
                "INSERT INTO proxy_logs VALUES (?,?,?)",
                (
                    "session-1",
                    "2026-08-04T00:00:03Z",
                    json.dumps({"model": "gpt-5.6-sol", "http_status": 200, "duration_ms": 99}),
                ),
            )
            connection.commit()
            connection.close()
            result = tap_observer.observe(path)
        self.assertEqual(result["tap"]["last_api"]["duration_ms"], 99)
        self.assertEqual(result["tap"]["last_activity_at"], "2026-08-04T00:00:03Z")

    def test_compact_record_restores_pending_function_call(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "trace.sqlite3"
            connection = create_trace(path)
            payload = {
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "exec_command",
                            "arguments": {"cmd": "mesh-compare --voxblame-dir state"},
                        }
                    ]
                }
            }
            connection.execute(
                "INSERT INTO record_blobs VALUES (?,?,?,?)",
                ("blob-1", "session-1", 1, json.dumps(payload)),
            )
            insert_record(
                connection,
                1,
                {"type": "compact-record-v1", "blob_id": "blob-1"},
            )
            connection.close()
            result = tap_observer.observe(path)
        self.assertEqual(result["activity"]["kind"], "awaiting_tool_result")
        self.assertEqual(result["activity"]["classification"], "voxblame")
        self.assertNotIn("mesh-compare", json.dumps(result))

    def test_matching_function_output_clears_pending(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "trace.sqlite3"
            connection = create_trace(path)
            insert_record(
                connection,
                1,
                {"type": "function_call", "call_id": "call-1", "name": "exec_command", "arguments": {"cmd": "git commit"}},
            )
            insert_record(
                connection,
                2,
                {"type": "function_call_output", "call_id": "call-1", "output": "secret result"},
            )
            connection.close()
            result = tap_observer.observe(path)
        self.assertEqual(result["activity"]["kind"], "tool_completed")
        self.assertEqual(result["activity"]["classification"], "checkpoint")
        self.assertNotIn("secret result", json.dumps(result))

    def test_classifier_is_deterministic_and_redacted(self) -> None:
        cases = {
            "mesh-inspect input.ply": "inspect",
            "python build123d model.py": "reconstruct",
            "cad snapshot model.step": "export",
            "mesh-to-cad-workspace begin-attempt": "workspace",
            "voxblame-prepare-reference input.ply": "canonical_preparation",
            "voxblame-measure candidate.glb": "measurement",
            "voxblame-preview candidate.glb": "preview",
            "voxblame-region-diff --repair-batch plan.json": "repair",
            "mesh-to-cad-workspace finalize --selection chosen.json": "final_rebuild",
            "python skills/cad/scripts/canonical-build --job candidate.json": "reconstruct",
            "voxblame-verify rebuilt.glb": "verification",
            "open reviews/final.png": "review",
            "git commit -m done": "checkpoint",
            "curl https://secret.invalid/token": "other_tool",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(tap_observer.classify_activity("exec_command", command), expected)
        self.assertEqual(
            ("final_rebuild", "verification"),
            tap_observer.classify_phases(
                "exec_command",
                "mesh-to-cad-workspace finalize --selection chosen.json",
            ),
        )

    def test_unsupported_bad_json_and_missing_blob_fail_soft(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            root = Path(root_text)
            unsupported = root / "unsupported.sqlite3"
            connection = create_trace(unsupported, version=5)
            connection.close()
            self.assertEqual(tap_observer.observe(unsupported)["tap"]["availability"], "unsupported")

            broken = root / "broken.sqlite3"
            connection = create_trace(broken)
            connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?)",
                ("session-1", 1, 1, "now", "not-json"),
            )
            connection.commit()
            connection.close()
            self.assertEqual(tap_observer.observe(broken)["tap"]["availability"], "degraded")

            missing = root / "missing-blob.sqlite3"
            connection = create_trace(missing)
            insert_record(connection, 1, {"type": "compact-record-v1", "blob_id": "absent"})
            connection.close()
            self.assertEqual(tap_observer.observe(missing)["tap"]["availability"], "degraded")

            unknown = root / "unknown-compact.sqlite3"
            connection = create_trace(unknown)
            insert_record(connection, 1, {"type": "compact-record-v2", "blob_id": "x"})
            connection.close()
            self.assertEqual(tap_observer.observe(unknown)["tap"]["availability"], "degraded")

    def test_busy_database_fails_soft(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            path = Path(root_text) / "busy.sqlite3"
            connection = create_trace(path)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                result = tap_observer.observe(path)
            finally:
                connection.rollback()
                connection.close()
        self.assertIn(result["tap"]["availability"], {"unavailable", "degraded"})

    def test_observer_does_not_mutate_or_create_sidecars(self) -> None:
        with temporary_directory(prefix="tap-observer-") as root_text:
            root = Path(root_text)
            path = root / "trace.sqlite3"
            connection = create_trace(path)
            insert_record(connection, 1, {"response": {"model": "gpt"}})
            connection.close()
            before = path.read_bytes()
            with closing(sqlite3.connect(path)) as reader:
                version_before = reader.execute("PRAGMA user_version").fetchone()[0]
            tap_observer.observe(path)
            with closing(sqlite3.connect(path)) as reader:
                version_after = reader.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(version_before, version_after)
            self.assertFalse(Path(f"{path}-wal").exists())
            self.assertFalse(Path(f"{path}-shm").exists())
            self.assertFalse((root / ".write.lock").exists())


if __name__ == "__main__":
    unittest.main()
