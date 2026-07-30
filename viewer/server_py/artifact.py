"""Render-artifact status/build for /__cad/artifact (the STEP component-GLB package).

- Non-STEP entries are NOT owned by the STEP provider (ownsEntry on entry.file).
  Imported `.step`/`.stp` AND generated `.step.py` entries ARE owned and get a
  freshness check; a generated model with no built artifact reports `needs-build`
  and is built on demand (the viewer surfaces every generated model, built or not).
- Freshness is a PURE descriptor read (descriptor + payload existence; a generated
  entry re-hashes its recorded source closure, an imported `.step` compares
  stepHash) — no OCP, no cadgen import. STEP and generated-DXF packages run the
  SAME validator and the SAME content digest cadgen's CLI gate uses, so the two
  never disagree about staleness. State machine:
  ready | needs-build | generating | error.
- The build (POST) shells out to `cadgen.step_artifact`, keeping OCP out of the
  server process (crash/memory isolation).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

try:  # POSIX only; without fcntl the lock probe degrades to "never locked".
    import fcntl
except ImportError:  # pragma: no cover - not reachable on darwin/linux CI
    fcntl = None  # type: ignore[assignment]

from . import scanner, source_hash

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
_GENERATION_LOCK_SUFFIX = ".generation.lock"


def owns_step_entry(entry) -> bool:
    return bool(entry) and bool(_STEP_ENTRY_RE.search(str(entry.get("file") or "")))


def owns_dxf_entry(entry) -> bool:
    # Generated `.dxf.py` drawings only; a raw imported `.dxf` renders directly
    # from disk and never needs a build.
    return bool(entry) and scanner.is_dxf_generator_path(str(entry.get("file") or ""))


def owns_entry(entry) -> bool:
    return owns_step_entry(entry) or owns_dxf_entry(entry)


# --- pure freshness validation (shared by both package formats) ---
# Per-format descriptor names, package kinds, payload refs, and error codes. The
# validation ALGORITHM is identical for both; only this table differs.
_STEP_PACKAGE = {
    "descriptor": "assembly.json",
    "package_kind": "assembly-package",
    "missing": "missing_glb",
    "unreadable": "missing_step_topology",
    "unsupported": "unsupported_step_topology",
    "stale": "stale_step_artifact",
}
_DRAWING_PACKAGE = {
    "descriptor": scanner.DRAWING_DESCRIPTOR_NAME,
    "package_kind": scanner.DRAWING_PACKAGE_KIND,
    "missing": "missing_dxf_artifact",
    "unreadable": "missing_dxf_artifact",
    "unsupported": "unsupported_dxf_artifact",
    "stale": "stale_dxf_artifact",
}


def _step_payload_refs(descriptor):
    """Every component GLB the assembly descriptor claims."""
    components = descriptor.get("components") if isinstance(descriptor.get("components"), dict) else {}
    return [str((component or {}).get("glb") or "").strip() for component in components.values()]


def _drawing_payload_refs(descriptor):
    return [str(descriptor.get("dxf") or "").strip()]


def _validate_render_package(spec, source_path, payload_refs, model_folder):
    """Return (ok, code) — ok=True when fresh, else (False, <error_code>).

    One algorithm for both formats: the package dir and descriptor must exist and
    declare the expected kind, every payload file the descriptor names must be on
    disk, and then freshness is decided by provenance —

    * generated (``sourceKind: python``): the recorded source closure must still
      hash unchanged. This is the SAME content digest cadgen's CLI gate uses (see
      source_hash.py), so the two never disagree. A descriptor that records no
      usable closure is treated as STALE for both formats: it cannot be shown to be
      current, and a rebuild is cheap and self-correcting.
    * imported (STEP only): the on-disk file must still hash to ``stepHash``.
    """
    package_dir = scanner.render_package_dir(source_path)
    if not os.path.isdir(package_dir):
        return (False, spec["missing"])
    descriptor_path = os.path.join(package_dir, spec["descriptor"])
    try:
        with open(descriptor_path, "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return (False, spec["unreadable"])
    if not isinstance(descriptor, dict) or descriptor.get("kind") != spec["package_kind"]:
        return (False, spec["unsupported"])
    for ref in payload_refs(descriptor):
        if not ref or not os.path.isfile(os.path.join(package_dir, ref)):
            return (False, spec["missing"])
    if str(descriptor.get("sourceKind", "step")).strip().lower() == "python":
        if not os.path.isfile(source_path):
            return (False, "missing_source_path")
        closure = descriptor.get("sourceClosureFiles")
        if not isinstance(closure, list) or not closure:
            return (False, spec["stale"])
        if not source_hash.closure_hash_matches(
            descriptor.get("sourceClosureHash"), closure, model_folder
        ):
            return (False, spec["stale"])
        return (True, None)
    step_hash = str(descriptor.get("stepHash", "")).strip()
    if scanner._file_stats(source_path):
        current = scanner._sha256_file(source_path)
        if current and step_hash and step_hash != current:
            return (False, spec["stale"])
    return (True, None)


def validate_step_freshness(repo_root, source_path):
    """ok=True (fresh/ready) or (False, code). source_path is the entry's step path
    (the `.step.py` for a generated model, the `.step` for an imported one)."""
    del repo_root  # closure paths resolve against the model folder, not the repo root
    return _validate_render_package(
        _STEP_PACKAGE, source_path, _step_payload_refs, os.path.dirname(os.path.abspath(source_path))
    )


def validate_dxf_freshness(repo_root, source_path):
    """ok=True (fresh/ready) or (False, code). source_path is the `.dxf.py`
    generator; the package dir is entry-keyed exactly like the STEP package."""
    del repo_root
    return _validate_render_package(
        _DRAWING_PACKAGE, source_path, _drawing_payload_refs, os.path.dirname(os.path.abspath(source_path))
    )


# --- generation lock probe (mirrors cadgen's _internal/generation_status.py) ---
# The build holds a POSIX advisory lock (fcntl.flock) on this sentinel for its whole
# run. The kernel owns the state, so a crashed or killed build releases it with no
# stale window — there is no pid, heartbeat, or age check to get wrong. The lock is
# probed, never taken: a reader must not block a request thread.
def generation_lock_path(package_dir: str) -> str:
    d = str(package_dir or "").strip()
    if not d:
        return ""
    return os.path.join(os.path.dirname(d), "." + os.path.basename(d) + _GENERATION_LOCK_SUFFIX)


def generation_lock_active(lock_path: str) -> bool:
    """True when a build currently holds the model's lock.

    Non-blocking probe: try to take the lock ourselves. Success means nobody held it,
    so we release immediately and report idle. A missing sentinel means no build has
    ever run for this model, which is likewise idle.
    """
    if not lock_path or fcntl is None:
        return False
    try:
        handle = open(lock_path, "a+b")
    except OSError:
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # EWOULDBLOCK/EAGAIN — a builder holds it.
        return True
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def await_generation_lock(lock_path: str, timeout_ms: float = 180_000, poll_ms: float = 400) -> bool:
    """Wait until an in-flight build's lock clears; return True if cleared, False on timeout."""
    deadline = time.time() * 1000 + timeout_ms
    while generation_lock_active(lock_path) and time.time() * 1000 < deadline:
        time.sleep(poll_ms / 1000)
    return not generation_lock_active(lock_path)
