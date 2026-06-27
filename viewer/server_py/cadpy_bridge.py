"""Subprocess bridge to cadpy for the heavy STEP build/export ops.

Mirrors pythonStepArtifact.cadPythonEnv discovery and the spawn + last-JSON-line
parsing. We deliberately keep these as a SUBPROCESS (not in-process import) so the
OCP/OpenCascade kernel never loads into the long-lived server process — same
crash/memory isolation the Node backend gets by spawning cadpy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_PYTHONPATH_REL_CANDIDATES = [
    os.path.join("scripts", "packages", "cadpy", "src"),
    os.path.join("viewer", "packages", "cadpy", "src"),
    os.path.join("packages", "cadpy", "src"),
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


def cadpy_pythonpath(repo_root: str) -> str:
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


def run_cadpy(module: str, args, repo_root: str) -> dict:
    """Run `python -m <module> <args>` and return the last stdout JSON line as a
    dict, or {ok:false,error} on failure (matching the Node spawn helpers)."""
    env = dict(os.environ)
    pythonpath = cadpy_pythonpath(repo_root)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
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
    message = (proc.stderr or proc.stdout or f"cadpy {module} exited with code {proc.returncode}").strip()
    return {"ok": False, "exitCode": proc.returncode, "error": message}
