"""Bridge to cadgen for the heavy STEP build/export ops.

OCP/OpenCascade is kept OUT of the long-lived server process (crash/memory
isolation, same as the Node backend). Two execution paths share one contract,
``run_cadgen(module, args, repo_root) -> dict``:

* warm worker (default): a persistent :mod:`server_py.worker` subprocess imports
  OCP once and services every build/export in-process — no per-request cold start.
* cold subprocess (fallback): a fresh ``python -m <module>`` per request, used when
  the worker is disabled (``VIEWER_CAD_WORKER=0``) or a worker transport fault
  occurs. Always available, fully isolated.

Both call the SAME general cadgen callables; only warmth/lifetime differ.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_PYTHONPATH_REL_CANDIDATES = [
    os.path.join("scripts", "packages", "cadgen", "src"),
    os.path.join("viewer", "packages", "cadgen", "src"),
    os.path.join("packages", "cadgen", "src"),
]


def _find_up_dir(rel: str, start: str) -> str:
    cur = os.path.abspath(start or ".")
    while True:
        candidate = os.path.join(cur, rel)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def cadgen_pythonpath(repo_root: str) -> str:
    entries = []
    for env_name in ("VIEWER_CAD_PYTHONPATH", "CAD_PYTHONPATH", "VIEWER_CADPY_PYTHONPATH"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            entries.append(value)
    for rel in _PYTHONPATH_REL_CANDIDATES:
        found = _find_up_dir(rel, repo_root) or _find_up_dir(rel, os.getcwd())
        if found:
            entries.append(found)
    existing = str(os.environ.get("PYTHONPATH") or "").strip()
    if existing:
        entries.append(existing)
    deduped = []
    seen = set()
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return os.pathsep.join(deduped)


def run_cadgen(module: str, args, repo_root: str) -> dict:
    """Run a cadgen build/export op and return its payload dict (``{ok:false,error}``
    on failure). Prefers the warm worker; falls back to a cold subprocess on any
    worker spawn/transport fault so the path is always available."""
    try:
        from . import worker_client

        return worker_client.run_cadgen(module, args, repo_root)
    except worker_client._WorkerError:
        pass  # worker disabled or faulted -> cold subprocess below
    return run_cadgen_cold(module, args, repo_root)


def run_cadgen_cold(module: str, args, repo_root: str) -> dict:
    """Run `python -m <module> <args>` in a fresh subprocess and return the last
    stdout JSON line as a dict, or {ok:false,error} on failure."""
    env = dict(os.environ)
    pythonpath = cadgen_pythonpath(repo_root)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    # Byte-deterministic artifacts (drawing packages are content-addressed):
    # ezdxf's object ordering depends on Python hash randomization.
    env.setdefault("PYTHONHASHSEED", "0")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", module, *args],
            cwd=repo_root, env=env, capture_output=True, text=True,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    for line in reversed(proc.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except ValueError:
                break
    message = (proc.stderr or proc.stdout or f"cadgen {module} exited with code {proc.returncode}").strip()
    return {"ok": False, "exitCode": proc.returncode, "error": message}
