"""Render-artifact status/build for /__cad/artifact (the STEP component-GLB package).

Mirrors renderArtifact.mjs + stepArtifactProvider.mjs + the STEP freshness in
cadDirectoryScanner.mjs:

- Non-STEP entries are NOT owned by the STEP provider (ownsEntry on entry.file).
  Imported `.step`/`.stp` AND generated `.step.py` entries ARE owned and get a
  freshness check; a generated model with no built artifact reports `needs-build`
  and is built on demand (the viewer surfaces every generated model, built or not).
- Freshness is a PURE descriptor read (assembly.json + component existence; the
  imported `.step` also compares stepHash, the generated `.step.py` checks the
  source-closure mtimes) — no OCP. State machine:
  ready | needs-build | generating | error.
- The build (POST) shells out to `cadgen.step_artifact` exactly as Node does,
  keeping OCP out of the server process (crash/memory isolation).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from . import scanner

ARTIFACT_STATE_READY = "ready"
ARTIFACT_STATE_NEEDS_BUILD = "needs-build"
ARTIFACT_STATE_GENERATING = "generating"
ARTIFACT_STATE_ERROR = "error"

BUILDABLE_STEP_ARTIFACT_CODES = frozenset([
    "missing_glb", "missing_step_topology", "missing_edge_topology",
    "missing_surface_edge_attributes", "missing_selector_topology",
    "missing_source_path", "missing_step_hash", "stale_step_artifact",
    "unsupported_step_topology",
    # Generated-DXF drawing package codes (same buildable semantics).
    "missing_dxf_artifact", "stale_dxf_artifact", "unsupported_dxf_artifact",
])

# Owns imported `.step`/`.stp` and generated `.step.py`/`.stp.py` entries.
_STEP_ENTRY_RE = re.compile(r"\.(step|stp)(\.py)?$", re.IGNORECASE)
_GENERATION_LOCK_SUFFIX = ".generation.lock.json"
_LOCK_ACTIVE_MAX_AGE_MS = 30_000


def owns_step_entry(entry) -> bool:
    return bool(entry) and bool(_STEP_ENTRY_RE.search(str(entry.get("file") or "")))


def owns_dxf_entry(entry) -> bool:
    # Generated `.dxf.py` drawings only; a raw imported `.dxf` renders directly
    # from disk and never needs a build.
    return bool(entry) and scanner.is_dxf_generator_path(str(entry.get("file") or ""))


def owns_entry(entry) -> bool:
    return owns_step_entry(entry) or owns_dxf_entry(entry)


# --- pure freshness validation (imported .step component-GLB package) ---
def _validate_assembly_package_artifact(repo_root, source_path, glb_path):
    """Return (ok, code) — ok=True when fresh, else (False, <error_code>). None
    if glb_path is not a package directory (legacy monolith GLB; treated as
    missing by the caller)."""
    if not os.path.isdir(glb_path):
        return None
    descriptor_path = os.path.join(glb_path, "assembly.json")
    try:
        with open(descriptor_path, "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return (False, "missing_step_topology")
    if not descriptor or descriptor.get("kind") != "assembly-package":
        return (False, "unsupported_step_topology")
    components = descriptor.get("components") if isinstance(descriptor.get("components"), dict) else {}
    for component in components.values():
        ref = str((component or {}).get("glb") or "").strip()
        component_path = os.path.join(glb_path, ref) if ref else ""
        if not component_path or not os.path.isfile(component_path):
            return (False, "missing_glb")
    uses_python = str(descriptor.get("sourceKind", "step")).strip().lower() == "python"
    if uses_python:
        # Generated packages: source-closure mtime trigger. The descriptor's
        # sourcePath + sourceClosureFiles are relative to the MODEL folder (the dir
        # holding the .step.py = dirname(source_path)), NOT the __cadcache__ dir.
        identity = scanner._generator_source_path_from_metadata(
            repo_root, descriptor.get("sourcePath"), scanner.PYTHON_GENERATOR_BY_KIND.get("step", "gen_step"),
            os.path.dirname(source_path),
        )
        if not identity["sourcePath"] or not identity["filePath"]:
            return (False, "missing_source_path")
        desc_stats = scanner._file_stats(descriptor_path)
        closure = descriptor.get("sourceClosureFiles") if isinstance(descriptor.get("sourceClosureFiles"), list) else []
        if desc_stats and closure:
            model_folder = os.path.dirname(identity["filePath"])
            for rel in closure:
                st = scanner._file_stats(os.path.join(model_folder, str(rel or "")))
                if st and st.st_mtime_ns > desc_stats.st_mtime_ns:
                    return (False, "stale_step_artifact")
        return (True, None)
    # Imported STEP: the on-disk .step must still hash to descriptor.stepHash.
    step_hash = str(descriptor.get("stepHash", "")).strip()
    if scanner._file_stats(source_path):
        current = scanner._sha256_file(source_path)
        if current and step_hash and step_hash != current:
            return (False, "stale_step_artifact")
    return (True, None)


def validate_step_freshness(repo_root, source_path):
    """ok=True (fresh/ready) or (False, code). source_path is the entry's step
    path; the package dir is inline_step_glb_artifact_path_for_source(source_path)."""
    glb_path = scanner.inline_step_glb_artifact_path_for_source(source_path)
    package = _validate_assembly_package_artifact(repo_root, source_path, glb_path)
    if package is not None:
        return package
    # Legacy monolith GLB topology parsing is a TODO; a missing package reads as
    # buildable missing_glb (the common pre-build state).
    if not scanner._file_stats(glb_path):
        return (False, "missing_glb")
    return (False, "missing_step_topology")


# --- pure freshness validation (generated .dxf.py drawing package) ---
def validate_dxf_freshness(repo_root, source_path):
    """ok=True (fresh/ready) or (False, code). source_path is the `.dxf.py`
    generator; the package dir is entry-keyed exactly like the STEP package.
    Mirrors the generated branch of _validate_assembly_package_artifact: the
    descriptor is a pure JSON read and staleness is the source-closure mtime
    trigger against the descriptor mtime — no cadgen import."""
    del repo_root
    package_dir = scanner.inline_step_glb_artifact_path_for_source(source_path)
    if not os.path.isdir(package_dir):
        return (False, "missing_dxf_artifact")
    descriptor_path = os.path.join(package_dir, scanner.DRAWING_DESCRIPTOR_NAME)
    try:
        with open(descriptor_path, "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return (False, "missing_dxf_artifact")
    if not isinstance(descriptor, dict) or descriptor.get("kind") != scanner.DRAWING_PACKAGE_KIND:
        return (False, "unsupported_dxf_artifact")
    dxf_ref = str(descriptor.get("dxf") or "").strip()
    dxf_path = os.path.join(package_dir, dxf_ref) if dxf_ref else ""
    if not dxf_path or not os.path.isfile(dxf_path):
        return (False, "missing_dxf_artifact")
    if not os.path.isfile(source_path):
        return (False, "missing_source_path")
    desc_stats = scanner._file_stats(descriptor_path)
    closure = descriptor.get("sourceClosureFiles") if isinstance(descriptor.get("sourceClosureFiles"), list) else []
    model_folder = os.path.dirname(os.path.abspath(source_path))
    closure_paths = [os.path.join(model_folder, str(rel or "")) for rel in closure]
    if not closure_paths:
        closure_paths = [os.path.abspath(source_path)]
    if desc_stats:
        for path in closure_paths:
            st = scanner._file_stats(path)
            if st is None or st.st_mtime_ns > desc_stats.st_mtime_ns:
                return (False, "stale_dxf_artifact")
    return (True, None)


# --- generation lock reader (mirror of generationLock.mjs) ---
def generation_lock_path(package_dir: str) -> str:
    d = str(package_dir or "").strip()
    if not d:
        return ""
    return os.path.join(os.path.dirname(d), "." + os.path.basename(d) + _GENERATION_LOCK_SUFFIX)


def _process_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def generation_lock_active(lock_path: str, now_ms: float | None = None) -> bool:
    if not lock_path:
        return False
    try:
        with open(lock_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict) or str(payload.get("status", "")).strip().lower() != "running":
        return False
    if not _process_alive(payload.get("pid")):
        return False
    stamp = str(payload.get("updatedAt") or payload.get("startedAt") or "")
    updated_ms = _parse_iso_ms(stamp)
    if updated_ms is None:
        return True
    now = now_ms if now_ms is not None else time.time() * 1000
    return (now - updated_ms) <= _LOCK_ACTIVE_MAX_AGE_MS


def await_generation_lock(lock_path: str, timeout_ms: float = 180_000, poll_ms: float = 400) -> bool:
    """Wait until an in-flight build's lock clears; return True if cleared, False on timeout."""
    deadline = time.time() * 1000 + timeout_ms
    while generation_lock_active(lock_path) and time.time() * 1000 < deadline:
        time.sleep(poll_ms / 1000)
    return not generation_lock_active(lock_path)


def _parse_iso_ms(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp() * 1000
    except (ValueError, OverflowError):
        return None
