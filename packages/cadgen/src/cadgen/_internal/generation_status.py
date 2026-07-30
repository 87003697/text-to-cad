"""Per-model build lock for the ``__cadgen__`` render package.

A POSIX advisory lock (``fcntl.flock``) on a bare sentinel file beside the model's
package directory. The KERNEL owns the lock state: it is released when the holding
file descriptor closes, including when the process crashes or is killed. Nothing is
written into the file and nothing reads its contents.

This replaced a JSON status file carrying ``{pid, status, startedAt, updatedAt}``
refreshed by a 1s heartbeat thread, which had three defects a real lock does not:

* It was never acquired, only written. Two concurrent builds of the same model both
  proceeded, the second overwrote the first's pid, and whichever finished first
  unlinked the shared file while the other was still writing into the package — so a
  reader saw "no build in flight" over a half-written package.
* Liveness was inferred from ``os.kill(pid, 0)`` plus a 30s heartbeat window. OCP
  meshing holds the GIL inside C for long stretches, so the heartbeat thread could
  starve and a healthy build would read as dead.
* Producers never waited for each other; only the viewer waited.

Blocking acquisition gives all three for free: a second producer (another CLI, or the
viewer's warm worker) waits, then finds the package current and no-ops via its own
freshness gate.

The sentinel is intentionally NOT unlinked on release. Unlinking races: a waiter that
already opened the file would hold a descriptor to an unlinked inode and "acquire" a
lock nobody else can see. The file is zero bytes and lives under gitignored
``__cadgen__``.
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path
from typing import Iterator

try:  # POSIX only; on a platform without fcntl the lock degrades to a no-op.
    import fcntl
except ImportError:  # pragma: no cover - not reachable on darwin/linux CI
    fcntl = None  # type: ignore[assignment]


GENERATION_LOCK_SUFFIX = ".generation.lock"
_HELD = threading.local()


def generation_lock_path(package_dir: Path | str) -> Path:
    """The single per-model generation lock, beside the model's __cadgen__ package directory.

    For a package dir ``<folder>/__cadgen__/models/<name>.step`` the lock is the hidden sibling
    ``<folder>/__cadgen__/models/.<name>.step.generation.lock``. It lives under ``__cadgen__``
    (gitignored) and is a fixed path per model, so the viewer can probe it during the artifact
    pull to coordinate with an in-flight build (see the viewer's server_py/artifact.py probe)."""
    package = Path(package_dir)
    return package.parent / f".{package.name}{GENERATION_LOCK_SUFFIX}"


def track_generation_run(lock_path: Path | str | None) -> contextlib.AbstractContextManager[None]:
    """Hold the model's generation lock for the duration of a build.

    Acquisition BLOCKS, so a concurrent build of the same model waits here instead of
    duplicating the work; when it proceeds its own freshness gate finds the package current.
    ``None`` (e.g. a non-package generator) is a no-op.
    """
    if lock_path is None or fcntl is None:
        return contextlib.nullcontext()
    return _flock(Path(lock_path))


@contextlib.contextmanager
def _flock(lock_path: Path) -> Iterator[None]:
    # Re-entrancy is per-thread AND per-path: a parent build that triggers a child build of the
    # SAME model in-process must not deadlock on itself (flock is per-fd, so a second open in
    # this process would block forever against our own held lock). A different model in the same
    # thread still takes its own lock.
    held: set[str] = getattr(_HELD, "paths", None) or set()
    _HELD.paths = held
    key = str(lock_path)
    if key in held:
        yield
        return
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError:
        # An unwritable package dir must not fail the build; degrade to no coordination.
        yield
        return
    held.add(key)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        held.discard(key)
        handle.close()
