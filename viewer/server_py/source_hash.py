"""Closure-hash mirror of ``cadgen._internal.source_hash`` (stdlib only).

``server_py`` must stay importable without cadgen/OCP installed, and cadgen is
discovered per-request (see ``cadgen_bridge.cadgen_pythonpath``) rather than being
on the server's ``sys.path``. So — like ``scanner.py``'s render-package path helpers —
the freshness digest is re-implemented here against ``ast`` + ``hashlib`` alone.

This exists so the viewer and the CLI answer "is this package stale?" the SAME way.
The viewer previously compared closure-file mtimes against the descriptor mtime while
cadgen compared content hashes, which disagreed in both directions: ``touch`` on a
generator made the viewer rebuild while the CLI reported "is current; skipped", and a
content edit that preserved mtime fooled the viewer but not the CLI. The rebuild that
followed a spurious mtime trigger was a no-op that existed only to bump the descriptor
mtime and quiet the trigger.

``tests/python/global/test_viewer_cadgen_mirror.py`` pins this against the cadgen
implementation over a corpus (comment-only edits, docstring edits, syntax errors,
non-Python inputs); any drift fails there.
"""

from __future__ import annotations

import ast
import hashlib
import os
import time

# Digest prefix for the AST-based (comment-insensitive) hash. Must match cadgen.
_SEMANTIC_PREFIX = "ast1:"

# (path) -> (st_mtime_ns, st_size, hash). Mirrors cadgen's memo, including the settle
# window: a file is only cached once its mtime is old enough that a same-size rewrite
# in the same coarse clock tick cannot hide behind the cached stat. Freshness runs on
# every artifact-status poll, so without this the viewer would re-parse every closure
# file of every model on each poll.
_SEMANTIC_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
_SEMANTIC_HASH_SETTLE_NS = 2_000_000_000


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def semantic_source_hash(path: str) -> str:
    """Comment/format-insensitive content hash for ``.py``; byte hash otherwise.

    Hashes ``ast.dump(ast.parse(...))`` without position attributes, so a comment or
    whitespace edit to a generator or shared helper does not invalidate a package,
    while every semantic change (docstrings included) does. Falls back to the byte
    hash when the source cannot be read or parsed — a syntactically broken generator
    must never read as unchanged.
    """
    if not path.endswith(".py"):
        return sha256_file(path)
    try:
        stat = os.stat(path)
    except OSError:
        stat = None
    if stat is not None:
        cached = _SEMANTIC_HASH_CACHE.get(path)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
    try:
        with open(path, "rb") as handle:
            dumped = ast.dump(ast.parse(handle.read()))
    except (OSError, SyntaxError, ValueError, MemoryError, RecursionError):
        result = sha256_file(path)
    else:
        result = _SEMANTIC_PREFIX + hashlib.sha256(dumped.encode("utf-8")).hexdigest()
    if stat is not None and time.time_ns() - stat.st_mtime_ns > _SEMANTIC_HASH_SETTLE_NS:
        _SEMANTIC_HASH_CACHE[path] = (stat.st_mtime_ns, stat.st_size, result)
    return result


def _closure_hash_for_pairs(pairs) -> str:
    digest = hashlib.sha256()
    for rel, file_hash in sorted(pairs):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve_against_base(relative: str, base: str):
    """Resolve a descriptor-recorded (base-relative or absolute) closure path."""
    rel = str(relative or "").strip()
    if not rel:
        return None
    candidate = rel if os.path.isabs(rel) else os.path.join(base, rel)
    resolved = os.path.realpath(candidate)
    return resolved if os.path.isfile(resolved) else None


def _recompute_closure_hash(relative_files, base: str, hasher):
    pairs = []
    for relative in relative_files:
        rel = str(relative or "").strip()
        if not rel:
            continue
        resolved = _resolve_against_base(rel, base)
        if resolved is None:
            return None
        try:
            pairs.append((rel, hasher(resolved)))
        except OSError:
            return None
    if not pairs:
        return None
    return _closure_hash_for_pairs(pairs)


def closure_hash_matches(recorded_hash, relative_files, base: str) -> bool:
    """Whether a descriptor's recorded closure hash still matches the sources on disk.

    Accepts EITHER the semantic recompute OR the legacy byte recompute, exactly as
    cadgen does: descriptors written before comment-insensitive hashing recorded a
    byte digest and must keep validating until their next genuine rebuild. A missing
    closure file is never a match — the caller rebuilds.
    """
    recorded = str(recorded_hash or "").strip()
    if not recorded:
        return False
    base_dir = os.path.realpath(base)
    for hasher in (semantic_source_hash, sha256_file):
        current = _recompute_closure_hash(relative_files, base_dir, hasher)
        if current is not None and current == recorded:
            return True
    return False
