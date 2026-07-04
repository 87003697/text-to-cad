from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


GENERATION_STATUS_SCHEMA_VERSION = 1
GENERATION_LOCK_SUFFIX = ".generation.lock.json"
_HEARTBEAT_INTERVAL_SEC = 1.0
_ACTIVE_RUN = threading.local()


def generation_lock_path(package_dir: Path | str) -> Path:
    """The single per-model generation lock, beside the model's __cadcache__ package directory.

    For a package dir ``<folder>/__cadcache__/models/<name>.step`` the lock is the hidden sibling
    ``<folder>/__cadcache__/models/.<name>.step.generation.lock.json``. It lives under
    ``__cadcache__`` (gitignored) and is a fixed path per model, so the viewer can read it during
    the artifact pull to coordinate with an in-flight build (see viewer generationLock.mjs)."""
    package = Path(package_dir)
    return package.parent / f".{package.name}{GENERATION_LOCK_SUFFIX}"


def track_generation_run(lock_path: Path | str | None) -> contextlib.AbstractContextManager[None]:
    """Hold a generation lock for the duration of a build.

    Writes a heartbeated lock file at ``lock_path`` while the build runs and removes it on exit, so
    a concurrent viewer/CLI build can detect the in-flight run (live pid + recent heartbeat) and
    wait for it instead of duplicating the work. ``None`` (e.g. a non-package generator) is a no-op.
    """
    if lock_path is None:
        return contextlib.nullcontext()
    return _GenerationLock(Path(lock_path)).run()


class _GenerationLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.pid = os.getpid()
        self._started_at = _now_iso()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @contextlib.contextmanager
    def run(self) -> Iterator[None]:
        # Re-entrancy: a parent build that triggers a child build in-process must not write or clean
        # up a competing lock — only the outermost run owns the lock file.
        depth = int(getattr(_ACTIVE_RUN, "depth", 0) or 0)
        if depth > 0:
            yield
            return
        _ACTIVE_RUN.depth = depth + 1
        self._write(status="running")
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        try:
            yield
        finally:
            try:
                self._stop.set()
                if self._thread is not None:
                    self._thread.join(timeout=0.25)
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    self._write(status="finished")
            finally:
                _ACTIVE_RUN.depth = depth

    def _heartbeat(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SEC):
            self._write(status="running")

    def _write(self, *, status: str) -> None:
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.lock_path.with_name(f"{self.lock_path.name}.{self.pid}.tmp")
            payload = {
                "schemaVersion": GENERATION_STATUS_SCHEMA_VERSION,
                "pid": self.pid,
                "status": status,
                "startedAt": self._started_at,
                "updatedAt": _now_iso(),
            }
            tmp_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            tmp_path.replace(self.lock_path)
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat().replace("+00:00", "Z")
