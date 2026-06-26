from __future__ import annotations

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
    # root under normal invocation) plus the cadpy package root. No frozen import-time global.
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


def repo_local_loaded_modules(module_names: object) -> dict[str, Path]:
    """Map of ``sys.modules`` names (restricted to those given) to their first-party ``.py``
    source files: every loaded module whose file is NOT under the interpreter's stdlib /
    site-packages roots. Working-directory independent — see :func:`_interpreter_roots`."""
    interpreter_roots = _interpreter_roots()
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
        if path.suffix == ".py" and not any(_is_within(path, root) for root in interpreter_roots):
            result[name] = path
    return result


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
            file_hash = _sha256_file(path)
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
) -> PythonSourceClosure:
    """Capture a generator's import closure after running it.

    ``before_module_names`` is ``set(sys.modules)`` sampled immediately before
    the generator module was loaded; the newly imported repo-local modules are
    its dependency closure. ``extra_files`` folds additional inputs into the
    closure — used by assemblies to include the child STEP files they compose
    from, so the closure hash also captures "a referenced child changed". Every
    recorded path is relative to ``base`` (the model folder).
    """
    import sys

    new_names = set(sys.modules) - set(before_module_names)
    dependency_files = [*repo_local_loaded_modules(new_names).values(), *extra_files]
    return closure_for_files(script_path, dependency_files, base=base)


def closure_hash_from_files(relative_files: object, *, base: Path) -> str | None:
    """Recompute a closure hash from a previously recorded ``base``-relative file list.

    Returns ``None`` when any recorded file is missing, which callers treat as
    "stale" (rebuild rather than risk reusing geometry built from absent
    sources)."""
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
            pairs.append((rel, _sha256_file(resolved)))
        except OSError:
            return None
    if not pairs:
        return None
    return _closure_hash_for_pairs(pairs)
