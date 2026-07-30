#!/usr/bin/env python3
"""Run one command through a mandatory per-experiment claude-tap proxy."""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Callable, Mapping


READY_PATTERN = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
FINAL_SESSION_STATUSES = {"complete", "error", "empty"}
DEFAULT_TAP_TARGET = "http://v2.open.venus.oa.com/llmproxy/v1"


class TapError(RuntimeError):
    """The mandatory proxy could not satisfy its runtime contract."""


class SignalRelay:
    """Record INT/TERM and forward them to the active workload process group."""

    def __init__(self) -> None:
        """Initialize signal state without changing the caller's handlers."""

        self.signum: int | None = None
        self.child: subprocess.Popen[bytes] | None = None
        self._previous: dict[int, signal.Handlers] = {}

    @property
    def cancelled(self) -> bool:
        """Return whether the supervisor received INT or TERM."""

        return self.signum is not None

    def attach(self, child: subprocess.Popen[bytes]) -> None:
        """Attach the workload and replay any signal received during Popen."""

        self.child = child
        if self.signum is not None and child.poll() is None:
            # Popen and attach are separate Python operations. Replaying here
            # closes the narrow window in which the supervisor had no child.
            signal_process_group(child, self.signum)

    def detach(self) -> None:
        """Stop forwarding after the workload has exited."""

        self.child = None

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        """Forward the first signal and make a repeated request forceful."""

        repeated = self.signum is not None
        if self.signum is None:
            self.signum = signum
        if self.child is not None and self.child.poll() is None:
            # A second Ctrl-C means the caller no longer wants graceful wait.
            signal_process_group(
                self.child,
                signal.SIGKILL if repeated else signum,
            )

    def __enter__(self) -> SignalRelay:
        """Install temporary handlers for one supervised lifecycle."""

        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Restore the original handlers and propagate caller exceptions."""

        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        return False


def signal_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    """Signal bwrap and all descendants without searching the process table."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def normalize_returncode(returncode: int) -> int:
    """Translate Popen's negative signal status into shell 128+signal form."""

    return 128 + abs(returncode) if returncode < 0 else returncode


def read_timeout(
    environ: Mapping[str, str],
    name: str,
    default: str,
) -> float:
    """Read one finite, non-negative timeout before any child is started."""

    raw = environ.get(name, default)
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise TapError(f"{name} must be numeric") from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise TapError(f"{name} must be finite and non-negative")
    return timeout


def resolve_tap(environ: Mapping[str, str]) -> str:
    """Find claude-tap, installing the default command through uv if absent."""

    requested = environ.get("CLAUDE_TAP_BIN", "claude-tap")
    path = shutil.which(requested, path=environ.get("PATH"))
    if path:
        return path
    if requested != "claude-tap":
        # An explicit override is a hard choice; do not silently install a
        # different binary and make tests or operations use the wrong version.
        raise TapError(f"CLAUDE_TAP_BIN not found: {requested}")

    uv_requested = environ.get("UV_BIN", "uv")
    uv_path = shutil.which(uv_requested, path=environ.get("PATH"))
    if not uv_path:
        raise TapError("claude-tap and uv are both unavailable")
    try:
        subprocess.run(
            [uv_path, "tool", "install", "--quiet", "claude-tap"],
            check=True,
            env=dict(environ),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TapError(f"failed to install claude-tap: {exc}") from exc

    path = shutil.which("claude-tap", path=environ.get("PATH"))
    if not path:
        raise TapError("claude-tap unavailable after uv install")
    return path


def start_tap(
    tap_bin: str,
    exp_dir: Path,
    environ: Mapping[str, str],
) -> subprocess.Popen[bytes]:
    """Start one loopback-only proxy whose database belongs to EXP_DIR."""

    tap_env = dict(environ)
    # Per-EXP storage prevents concurrent pilots from sharing trace state.
    # Unbuffered output makes the ready marker observable immediately.
    tap_env["CLOUDTAP_DB"] = str(exp_dir / "traces.sqlite3")
    tap_env["PYTHONUNBUFFERED"] = "1"
    argv = [
        tap_bin,
        "--tap-client",
        "codex",
        "--tap-no-launch",
        "--tap-no-open",
        "--tap-no-live",
        "--tap-host",
        "127.0.0.1",
        "--tap-port",
        # claude-tap owns bind(0), avoiding a reserve-close-rebind race.
        "0",
        "--tap-target",
        environ.get("CLAUDE_TAP_TARGET", DEFAULT_TAP_TARGET),
        "--tap-allow-path",
        "/v1",
        "--tap-max-traces",
        # Each EXP has its own DB, so retention must not delete pilot evidence.
        "0",
    ]
    try:
        log_file = (exp_dir / ".claude-tap.log").open("wb")
    except OSError as exc:
        raise TapError(f"cannot open claude-tap log: {exc}") from exc
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=tap_env,
            # Tap is a direct child but not part of the workload process group.
            start_new_session=True,
        )
    except OSError as exc:
        raise TapError(f"failed to start claude-tap: {exc}") from exc
    finally:
        log_file.close()


def wait_ready(
    process: subprocess.Popen[bytes],
    log_path: Path,
    timeout: float,
    cancelled: Callable[[], bool],
) -> int | None:
    """Return the child-advertised port, or None when startup was cancelled."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            return None
        returncode = process.poll()
        if returncode is not None:
            raise TapError(f"claude-tap exited before ready (status={returncode})")
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise TapError(f"cannot read claude-tap log: {exc}") from exc
        match = READY_PATTERN.search(text)
        if match is not None:
            # The log is truncated for this exact child at start_tap(), so a
            # matched dynamic port cannot be stale state from another pilot.
            return int(match.group(1))
        time.sleep(0.05)
    raise TapError("claude-tap readiness timeout")


def stop_tap(process: subprocess.Popen[bytes], timeout: float) -> None:
    """Finalize tap with SIGINT, escalating only after bounded waits."""

    if process.poll() is not None:
        return
    # claude-tap 0.1.140 finalizes the SQLite writer on KeyboardInterrupt.
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        print("warning: claude-tap SIGINT timeout; sending SIGTERM", file=sys.stderr)
    process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        print("warning: claude-tap SIGTERM timeout; sending SIGKILL", file=sys.stderr)
    process.kill()
    process.wait(timeout=1)


def wait_workload(
    workload: subprocess.Popen[bytes],
    tap: subprocess.Popen[bytes],
) -> tuple[int, bool]:
    """Wait for workload while failing closed if the mandatory proxy exits."""

    while True:
        workload_status = workload.poll()
        if workload_status is not None:
            return normalize_returncode(workload_status), False
        tap_status = tap.poll()
        if tap_status is not None:
            print(
                f"pilot-tap-supervisor: claude-tap exited during workload "
                f"(status={tap_status})",
                file=sys.stderr,
            )
            # Without tap the workload must not continue and possibly retry a
            # direct provider path. Terminate the whole bwrap process group.
            signal_process_group(workload, signal.SIGTERM)
            try:
                workload.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_process_group(workload, signal.SIGKILL)
                workload.wait(timeout=2)
            return 1, True
        time.sleep(0.1)


def read_trace(exp_dir: Path) -> tuple[str, str, int]:
    """Return the latest session id, status, and captured record count."""

    db_path = exp_dir / "traces.sqlite3"
    if not db_path.is_file():
        raise TapError("required traces.sqlite3 is missing")
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT id, status, record_count "
                "FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise TapError(f"cannot query traces.sqlite3: {exc}") from exc
    if row is None:
        raise TapError("traces.sqlite3 contains no session")
    return str(row[0]), str(row[1]), int(row[2])


def export_html(
    tap_bin: str,
    exp_dir: Path,
    session_id: str,
    environ: Mapping[str, str],
) -> None:
    """Best-effort export of an atomic HTML viewer from finalized SQLite."""

    temporary = exp_dir / f".trace.html.tmp.{os.getpid()}"
    output = exp_dir / "trace.html"
    export_log = exp_dir / ".claude-tap.log.export"
    temporary.unlink(missing_ok=True)
    export_env = dict(environ)
    export_env["CLOUDTAP_DB"] = str(exp_dir / "traces.sqlite3")
    try:
        with export_log.open("wb") as log_file:
            result = subprocess.run(
                [
                    tap_bin,
                    "export",
                    session_id,
                    "--format",
                    "html",
                    "--output",
                    str(temporary),
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=export_env,
                check=False,
            )
        if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size:
            # Publish only a complete viewer; a failed export never overwrites
            # an older valid trace.html.
            temporary.replace(output)
            export_log.unlink(missing_ok=True)
        else:
            print(
                "warning: trace.html export failed; SQLite and export log preserved",
                file=sys.stderr,
            )
    except OSError as exc:
        print(f"warning: trace.html export failed: {exc}", file=sys.stderr)
    finally:
        temporary.unlink(missing_ok=True)


def run_supervised(
    exp_dir: Path,
    command: list[str],
    environ: Mapping[str, str],
) -> int:
    """Run command behind mandatory tap and return a shell-compatible status."""

    tap_bin = resolve_tap(environ)
    # Validate timeouts before Popen so malformed cleanup configuration cannot
    # leave a proxy whose stop policy is unknown.
    ready_timeout = read_timeout(environ, "TAP_READY_TIMEOUT", "5")
    stop_timeout = read_timeout(environ, "TAP_STOP_TIMEOUT", "5")

    child_status: int | None = None
    tap_failed = False
    tap_exited_before_stop = False
    trace_valid = False

    # Install signal handlers before tap Popen. This prevents an INT/TERM in
    # the start_tap -> relay-enter window from orphaning the new proxy.
    with SignalRelay() as relay:
        tap = start_tap(tap_bin, exp_dir, environ)
        try:
            port = wait_ready(
                tap,
                exp_dir / ".claude-tap.log",
                ready_timeout,
                lambda: relay.cancelled,
            )
            if port is not None:
                child_env = dict(environ)
                child_env["CLAUDE_TAP_URL"] = f"http://127.0.0.1:{port}/v1"
                workload = subprocess.Popen(
                    command,
                    # Inherit wrapper redirections so Codex continues to write
                    # stderr.log and remains non-interactive exactly as before.
                    stdin=None,
                    stdout=None,
                    stderr=None,
                    env=child_env,
                    start_new_session=True,
                )
                relay.attach(workload)
                try:
                    child_status, tap_failed = wait_workload(workload, tap)
                finally:
                    relay.detach()
        finally:
            tap_exited_before_stop = tap.poll() is not None
            try:
                stop_tap(tap, stop_timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"warning: failed to stop claude-tap: {exc}", file=sys.stderr)
                tap_failed = True

        try:
            session_id, session_status, record_count = read_trace(exp_dir)
        except TapError as exc:
            print(f"pilot-tap-supervisor: {exc}", file=sys.stderr)
        else:
            trace_valid = session_status in FINAL_SESSION_STATUSES
            if not trace_valid:
                print(
                    f"pilot-tap-supervisor: trace session remains "
                    f"{session_status!r}",
                    file=sys.stderr,
                )
            elif child_status == 0 and record_count == 0:
                # A zero-request trace cannot prove that successful Codex
                # traffic actually passed through the mandatory proxy.
                print(
                    "pilot-tap-supervisor: successful Codex run captured no requests",
                    file=sys.stderr,
                )
                trace_valid = False
            export_html(tap_bin, exp_dir, session_id, environ)

        # Preserve the public priority explicitly: caller signal first, then
        # mandatory tap/trace health, then the workload's own status.
        if relay.signum is not None:
            return 128 + relay.signum
        if tap_failed or tap_exited_before_stop or not trace_valid:
            return 1
        if child_status is None:
            return 1
        return child_status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse EXP_DIR and the exact workload argv following `--`."""

    parser = argparse.ArgumentParser(
        description="Run one command through mandatory claude-tap"
    )
    parser.add_argument("exp_dir", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing workload after --")
    return args


def main(argv: list[str] | None = None) -> int:
    """CLI adapter with concise operator-facing errors."""

    args = parse_args(argv)
    exp_dir = args.exp_dir.resolve()
    if not exp_dir.is_dir():
        print(f"pilot-tap-supervisor: EXP_DIR not found: {exp_dir}", file=sys.stderr)
        return 1
    try:
        return run_supervised(exp_dir, args.command, dict(os.environ))
    except (OSError, TapError) as exc:
        print(f"pilot-tap-supervisor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
