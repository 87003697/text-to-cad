"""Run one Python command in a kill-on-close Windows Job Object.

The viewer launches this wrapper on Windows and owns only the wrapper
process handle.  If the viewer terminates the wrapper at its deadline,
Windows closes the job handle and atomically terminates the command and
all descendants, including children that inherited stdout/stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_INFINITE = 0xFFFFFFFF
_WAIT_FAILED = 0xFFFFFFFF


def _run_in_job(argv: Sequence[str]) -> int:
    if os.name != "nt":
        raise OSError("Windows Job Object runner requires os.name == 'nt'")

    import ctypes
    from ctypes import wintypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMITS(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMITS),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process: subprocess.Popen[bytes] | None = None
    try:
        limits = _EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(
            [sys.executable, *argv],
            creationflags=_CREATE_SUSPENDED,
        )
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
            process.kill()
            raise ctypes.WinError(ctypes.get_last_error())
        status = ntdll.NtResumeProcess(int(process._handle))
        if status != 0:
            raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08X}")
        return_code = process.wait()
        # A Job Object becomes signaled only when its active process
        # count reaches zero. Keep the wrapper (and therefore the job
        # handle) alive while descendants remain. If Node reaches its
        # deadline and kills this wrapper, KILL_ON_JOB_CLOSE atomically
        # terminates those descendants and releases inherited pipes.
        wait_result = kernel32.WaitForSingleObject(job, _INFINITE)
        if wait_result == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        return return_code
    finally:
        # KILL_ON_JOB_CLOSE is the fail-closed path for exceptions and
        # for external termination of this wrapper by the Node parent.
        kernel32.CloseHandle(job)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        raise SystemExit("usage: windows_job_runner -- <python arguments>")
    return _run_in_job(args)


if __name__ == "__main__":
    raise SystemExit(main())
