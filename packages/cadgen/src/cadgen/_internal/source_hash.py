from __future__ import annotations

import functools

import ast
import contextlib
import hashlib
import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PythonSourceHash:
    source_path: str
    source_hash: str


def python_source_hash(script_path: Path) -> PythonSourceHash:
    """Hash the generator script content and record its metadata path."""
    resolved_script = script_path.expanduser().resolve()
    return PythonSourceHash(
        source_path=_manifest_path(resolved_script),
        source_hash=_sha256_file(resolved_script),
    )


def _manifest_roots() -> tuple[Path, ...]:
    # Roots for displaying a generator's source_path manifest-relative: the live cwd (the repo
    # root under normal invocation) plus the cadgen package root. No frozen import-time global.
    return tuple(_dedupe_paths([
        Path.cwd().resolve(),
        _PACKAGE_ROOT.resolve(),
    ]))


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _manifest_path(path: Path) -> str:
    resolved = path.resolve()
    for root in _manifest_roots():
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_source_hash(path: Path) -> str:
    """Content hash that ignores comments, blank lines, and formatting for
    Python sources — so a comment/whitespace-only edit to a generator or a
    shared helper does not invalidate the model's closure — while staying
    sensitive to every semantic change (including docstrings). Non-``.py``
    closure inputs (e.g. composed child STEP files) keep the byte hash.

    Hashes the parsed AST dumped WITHOUT position attributes. A bytecode /
    ``marshal`` digest would NOT be comment-insensitive: inserting a comment
    line shifts ``co_firstlineno`` and the line table. ``ast.dump`` defaults to
    ``include_attributes=False``, which omits line/column numbers while keeping
    the full semantic structure. Falls back to the byte hash on an unreadable
    or unparseable source (so a syntactically broken generator is never treated
    as unchanged just because its AST could not be built)."""
    if path.suffix != ".py":
        return _sha256_file(path)
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return _sha256_file(path)
    return "ast1:" + hashlib.sha256(ast.dump(tree).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PythonSourceClosure:
    """Transitive local-import closure of a generator script.

    ``files`` lists the manifest-relative paths of the script plus every
    repository-local Python module it imported at run time (recursively).
    ``closure_hash`` is a stable digest of those paths and their contents.

    The closure is captured from ``sys.modules`` rather than by static analysis
    because the generators reach sibling/shared modules through computed
    ``sys.path`` insertions that static import resolution cannot follow.
    """

    closure_hash: str
    files: tuple[str, ...]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_manifest_path(relative: str) -> Path | None:
    """Inverse of ``_manifest_path``: resolve a stored relative path back to an
    existing file under one of the manifest roots."""
    rel = str(relative or "").strip()
    if not rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for root in _manifest_roots():
        resolved = (root / candidate).resolve()
        if resolved.is_file():
            return resolved
    return None


def _closure_hash_for_pairs(pairs: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for rel, file_hash in sorted(pairs):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def _interpreter_roots() -> tuple[Path, ...]:
    """Directories holding the Python interpreter, its standard library, and installed packages
    (the venv / site-packages). A loaded module whose file lives under any of these is third-party
    (build123d, OCP, the stdlib) and is excluded from a generator's source closure.

    This replaces an earlier repo-root containment test: first-party vs third-party is decided by
    the interpreter layout, NOT by the process working directory, so the closure a generator
    records is identical regardless of which directory the build was launched from.
    """
    roots: set[Path] = set()
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = paths.get(key)
        if value:
            roots.add(Path(value).resolve())
    for prefix in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        if prefix:
            roots.add(Path(prefix).resolve())
    return tuple(roots)


@functools.lru_cache(maxsize=1)
def _runtime_roots() -> tuple[Path, ...]:
    """Directories holding the RUNNING generation runtime itself: the active cadgen
    package and the CLI launcher script's directory. Runtime files are versioned and
    shipped separately from models, so — like the stdlib and site-packages — they are
    never a model's freshness input. In production cadgen lives in site-packages and
    is excluded by :func:`_interpreter_roots` already; these roots make dev checkouts
    (editable installs, vendored skill symlinks) behave identically."""
    roots: list[Path] = [_PACKAGE_ROOT.resolve()]
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if main_file:
        # Only a real on-disk launcher is runtime. Interactive / stdin / ``-c``
        # entry points set ``__file__`` to a placeholder like ``<stdin>`` or
        # ``<string>``, whose ``resolve().parent`` is the CWD — adding that would
        # wrongly mark the model folder (and its sibling helper modules) as
        # runtime, drop them from the recorded closure, and silently disable
        # staleness detection for stdin/`-c`-driven builds. ``is_file()`` rejects
        # every ``<...>`` placeholder while still catching the CLI launcher.
        try:
            resolved_main = Path(main_file).resolve()
        except OSError:
            resolved_main = None
        if resolved_main is not None and resolved_main.is_file():
            roots.append(resolved_main.parent)
    return tuple(roots)


@functools.lru_cache(maxsize=1)
def _excluded_roots() -> tuple[Path, ...]:
    # Environment-derived and stable for the life of the process; the closure
    # capture consults this per loaded-module path (hundreds of thousands of
    # lstat/realpath calls per entry when uncached, ~8% of a warm build).
    return (*_interpreter_roots(), *_runtime_roots())


def is_first_party_source_file(path: Path) -> bool:
    """True for a ``.py`` file that counts as model-side code: not stdlib, not
    site-packages, and not part of the running generation runtime."""
    return path.suffix == ".py" and not any(_is_within(path, root) for root in _excluded_roots())


def repo_local_loaded_modules(module_names: object) -> dict[str, Path]:
    """Map of ``sys.modules`` names (restricted to those given) to their first-party ``.py``
    source files: every loaded module whose file is NOT under the interpreter's stdlib /
    site-packages roots or the running runtime's own roots. Working-directory
    independent — see :func:`_interpreter_roots` / :func:`_runtime_roots`."""
    result: dict[str, Path] = {}
    for name in module_names:
        module = sys.modules.get(name)
        file_name = getattr(module, "__file__", None)
        if not file_name:
            continue
        try:
            path = Path(file_name).resolve()
        except OSError:
            continue
        if is_first_party_source_file(path):
            result[name] = path
    return result


def evict_first_party_modules() -> tuple[str, ...]:
    """Drop every first-party module from ``sys.modules`` and return the evicted names.

    Run BEFORE loading a generator: with a clean first-party module space, the
    generator's full dependency closure is freshly imported (and therefore freshly
    EXECUTED, which :func:`record_first_party_execution` observes) on every run —
    regardless of what earlier builds in the same process imported, whether a
    previous build failed partway, or what the generator unloads mid-run. Runtime
    and third-party modules (cadgen, build123d, OCP, ...) are never touched: they
    cannot reload safely and are not freshness inputs."""
    evicted = tuple(repo_local_loaded_modules(set(sys.modules)))
    for name in evicted:
        sys.modules.pop(name, None)
    return evicted


_ACTIVE_EXECUTION_CAPTURE: set[Path] | None = None
_AUDIT_HOOK_INSTALLED = False


def _execution_audit_hook(event: str, args: tuple) -> None:
    capture = _ACTIVE_EXECUTION_CAPTURE
    if capture is None or event != "exec" or not args:
        return
    file_name = getattr(args[0], "co_filename", None)
    if not file_name:
        return
    try:
        path = Path(file_name).resolve()
    except (OSError, ValueError):
        return
    if path.is_file() and is_first_party_source_file(path):
        capture.add(path)


@contextlib.contextmanager
def record_first_party_execution():
    """Record every first-party ``.py`` file EXECUTED while the context is active.

    Uses the ``exec`` audit event, which fires for each module body execution — via
    normal imports, ``importlib.util.spec_from_file_location`` path loads (which
    bypass ``sys.meta_path``), and bytecode-cached loads alike — so the recorded set
    is complete even if the generator unloads modules from ``sys.modules`` mid-run.
    Audit hooks are irremovable by design, so one process-global hook is installed
    lazily and stays dormant (a single ``None`` check per event) outside capture
    windows. Combined with :func:`evict_first_party_modules`, every first-party
    dependency is guaranteed to produce an execution event inside the window."""
    global _ACTIVE_EXECUTION_CAPTURE, _AUDIT_HOOK_INSTALLED
    if not _AUDIT_HOOK_INSTALLED:
        sys.addaudithook(_execution_audit_hook)
        _AUDIT_HOOK_INSTALLED = True
    recorded: set[Path] = set()
    previous = _ACTIVE_EXECUTION_CAPTURE
    _ACTIVE_EXECUTION_CAPTURE = recorded
    try:
        yield recorded
    finally:
        _ACTIVE_EXECUTION_CAPTURE = previous
        if previous is not None:
            previous |= recorded


def _relative_to_base(path: Path, base: Path) -> str:
    """A closure file's path relative to the model folder ``base`` (the directory that holds the
    generator source / logical STEP). Uses ``os.path.relpath`` so a sibling or parent file gets a
    clean ``../`` ref instead of an absolute or repo-root-anchored path — this keeps the closure
    (and the descriptor that records it) location-independent: the same model produces the same
    closure regardless of where the repository lives on disk."""
    return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def _resolve_against_base(relative: str, base: Path) -> Path | None:
    """Inverse of :func:`_relative_to_base`: resolve a ``base``-relative (or absolute) recorded
    closure path back to an existing file."""
    rel = str(relative or "").strip()
    if not rel:
        return None
    candidate = Path(rel)
    resolved = (candidate if candidate.is_absolute() else (base / candidate)).resolve()
    return resolved if resolved.is_file() else None


def closure_for_files(script_path: Path, files: object, *, base: Path) -> PythonSourceClosure:
    """Build a closure record from the script plus a set of dependency files, recording every path
    RELATIVE TO ``base`` (the model folder). The digest is computed over (relative path, content
    hash) pairs, so it — like the stored ``files`` — is independent of the absolute repository
    location."""
    base_dir = base.expanduser().resolve()
    paths: set[Path] = {script_path.expanduser().resolve()}
    for file in files:
        paths.add(Path(file).expanduser().resolve())
    pairs: list[tuple[str, str]] = []
    for path in paths:
        try:
            file_hash = _semantic_source_hash(path)
        except OSError:
            continue
        pairs.append((_relative_to_base(path, base_dir), file_hash))
    return PythonSourceClosure(
        closure_hash=_closure_hash_for_pairs(pairs),
        files=tuple(sorted(rel for rel, _ in pairs)),
    )


def capture_runtime_closure(
    before_module_names: object,
    script_path: Path,
    *,
    base: Path,
    extra_files: object = (),
    executed_files: object = (),
) -> PythonSourceClosure:
    """Capture a generator's dependency closure after running it.

    Two observation channels are unioned: ``executed_files`` — the first-party
    files recorded by :func:`record_first_party_execution` while the generator
    ran (complete even when the generator unloads modules mid-run) — and the
    ``sys.modules`` delta against ``before_module_names`` (a belt-and-braces
    catch for modules registered without a fresh body execution). ``extra_files``
    folds additional inputs into the closure — used by assemblies to include the
    child STEP files they compose from, so the closure hash also captures "a
    referenced child changed". Every recorded path is relative to ``base`` (the
    model folder).
    """
    import sys

    new_names = set(sys.modules) - set(before_module_names)
    dependency_files = [
        *repo_local_loaded_modules(new_names).values(),
        *executed_files,
        *extra_files,
    ]
    return closure_for_files(script_path, dependency_files, base=base)


def _recompute_closure_hash(relative_files: object, *, base: Path, hasher) -> str | None:
    base_dir = base.expanduser().resolve()
    pairs: list[tuple[str, str]] = []
    for relative in relative_files:
        rel = str(relative or "").strip()
        if not rel:
            continue
        resolved = _resolve_against_base(rel, base_dir)
        if resolved is None:
            return None
        try:
            pairs.append((rel, hasher(resolved)))
        except OSError:
            return None
    if not pairs:
        return None
    return _closure_hash_for_pairs(pairs)


def closure_hash_from_files(relative_files: object, *, base: Path) -> str | None:
    """Recompute the canonical (semantic) closure hash from a previously recorded
    ``base``-relative file list.

    Returns ``None`` when any recorded file is missing, which callers treat as
    "stale" (rebuild rather than risk reusing geometry built from absent
    sources)."""
    return _recompute_closure_hash(relative_files, base=base, hasher=_semantic_source_hash)


def closure_hash_matches(recorded_hash: object, relative_files: object, *, base: Path) -> bool:
    """Whether a recorded closure hash still matches the current sources.

    Accepts EITHER the current semantic (AST) recompute OR the legacy byte
    recompute: descriptors written before comment-insensitive hashing recorded a
    byte-based digest, and must keep validating (no mass rebuild on upgrade)
    until their next genuine rebuild re-records the semantic digest. A missing
    file (either recompute returns ``None``) is not a match — the caller rebuilds.
    """
    recorded = str(recorded_hash or "").strip()
    if not recorded:
        return False
    for hasher in (_semantic_source_hash, _sha256_file):
        current = _recompute_closure_hash(relative_files, base=base, hasher=hasher)
        if current is not None and current == recorded:
            return True
    return False
