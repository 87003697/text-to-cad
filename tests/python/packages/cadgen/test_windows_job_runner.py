from __future__ import annotations

import ctypes
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import windows_job_runner  # noqa: E402


class _Function:
    def __init__(self, implementation):
        self._implementation = implementation
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._implementation(*args)


class _Process:
    _handle = 202

    def __init__(self, events: list[str]):
        self._events = events

    def wait(self) -> int:
        self._events.append("process.wait")
        return 0

    def kill(self) -> None:
        self._events.append("process.kill")


class WindowsJobRunnerTests(unittest.TestCase):
    def test_success_closes_job_without_waiting_for_descendants(self) -> None:
        events: list[str] = []
        kernel32 = mock.Mock()
        kernel32.CreateJobObjectW = _Function(lambda *_: 101)
        kernel32.SetInformationJobObject = _Function(lambda *_: True)
        kernel32.AssignProcessToJobObject = _Function(lambda *_: True)
        kernel32.WaitForSingleObject = _Function(
            lambda *_: self.fail("must not wait indefinitely for descendants")
        )
        kernel32.CloseHandle = _Function(lambda *_: events.append("job.close") or True)
        ntdll = mock.Mock()
        ntdll.NtResumeProcess = _Function(lambda *_: 0)

        def load_library(name: str, **_kwargs):
            return kernel32 if name == "kernel32" else ntdll

        process = _Process(events)
        with (
            mock.patch.object(windows_job_runner.os, "name", "nt"),
            mock.patch.object(ctypes, "WinDLL", load_library, create=True),
            mock.patch.object(windows_job_runner.subprocess, "Popen", return_value=process),
        ):
            return_code = windows_job_runner._run_in_job(["-c", "print('ok')"])

        self.assertEqual(0, return_code)
        self.assertEqual(["process.wait", "job.close"], events)


if __name__ == "__main__":
    unittest.main()
