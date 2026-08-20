"""Tests for worker timeout handling, cold subprocess timeouts, and chunked streaming."""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import cadgen_bridge  # noqa: E402
from server_py import server as server_mod  # noqa: E402
from server_py import worker_client  # noqa: E402


class WorkerTimeoutTests(unittest.TestCase):
    def test_worker_read_line_timeout_raises_transport_error(self):
        worker = worker_client.CadWorker()
        proc = worker_client.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=worker_client.subprocess.PIPE,
            stdin=worker_client.subprocess.PIPE,
            text=True,
        )
        try:
            with self.assertRaises(worker_client._WorkerTransportError) as ctx:
                worker._read_line(proc, timeout=0.2)
            self.assertIn("timed out", str(ctx.exception))
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.wait()

    def test_worker_request_timeout_reaps_and_recovers(self):
        worker = worker_client.CadWorker()
        try:
            # First send a ping with a real timeout to ensure it works
            res = worker.ping(timeout=5.0)
            self.assertEqual(res.get("ok"), True)
            self.assertTrue(worker._alive())
            first_pid = worker._proc.pid if worker._proc else None

            # Next simulate a hung request with a short timeout by mocking _read_line
            # on the first attempt to timeout
            original_read_line = worker._read_line
            call_count = [0]

            def mock_read_line(proc, timeout=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise worker_client._WorkerTransportError("simulated timeout")
                return original_read_line(proc, timeout)

            with mock.patch.object(worker, "_read_line", side_effect=mock_read_line):
                # _request will catch the first _WorkerTransportError, reap the worker, and retry
                res = worker.ping(timeout=5.0)
                self.assertEqual(res.get("ok"), True)
                second_pid = worker._proc.pid if worker._proc else None
                # Worker should have been respawned with a new process PID
                self.assertNotEqual(first_pid, second_pid)
        finally:
            worker.close()

    def test_cadgen_bridge_cold_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = cadgen_bridge.run_cadgen_cold(
                "timeit",
                ["-s", "import time", "time.sleep(5)"],
                tmp,
                timeout=0.2,
            )
            self.assertEqual(result.get("ok"), False)
            self.assertIn("timed out", result.get("error", ""))


class StreamFileTests(unittest.TestCase):
    def test_stream_file_serves_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = pathlib.Path(tmp) / "large.bin"
            payload = b"X" * (128 * 1024)  # 128 KiB
            test_file.write_bytes(payload)

            handler = mock.MagicMock(spec=server_mod.Handler)
            handler.command = "GET"
            written_bytes = bytearray()
            handler.wfile = mock.MagicMock()
            handler.wfile.write.side_effect = lambda data: written_bytes.extend(data)

            ok = server_mod.Handler._stream_file(
                handler,
                str(test_file),
                "application/octet-stream",
            )
            self.assertTrue(ok)
            handler.send_response.assert_called_once_with(200)
            handler.send_header.assert_any_call("content-length", str(len(payload)))
            handler.send_header.assert_any_call("content-type", "application/octet-stream")
            self.assertEqual(bytes(written_bytes), payload)


if __name__ == "__main__":
    unittest.main()
