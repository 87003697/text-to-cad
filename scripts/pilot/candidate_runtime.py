"""Build the small, relocatable CAD runtime exposed to Agent code.

This is deliberately a runtime *view*, not a package manager.  The trusted
runner admits the fixed ``build123d`` distribution closure, the Python
standard-library/loader closure, and nothing else.  The resulting tree is
content-addressed, atomically published, and reused by later pilots.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import time
from typing import Iterable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None


class CandidateRuntimeError(RuntimeError):
    """The trusted CAD runtime could not be made safe and complete."""


@dataclass(frozen=True)
class FileRecord:
    """Hash/size facts from one installed distribution RECORD row."""

    path: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class DistributionRecord:
    """One installed distribution admitted by the fixed CAD closure."""

    name: str
    version: str
    location: Path
    files: tuple[str, ...]
    record_sha256: str
    file_records: tuple[FileRecord, ...] = ()


@dataclass(frozen=True)
class RuntimeProbe:
    """Interpreter and dependency facts returned by the trusted venv."""

    version: str
    stdlib: Path
    platstdlib: Path
    purelib: Path | None
    platlib: Path | None
    dynload: Path | None
    interpreter: Path | None = None
    base_prefix: Path | None = None
    exec_prefix: Path | None = None
    libdir: Path | None = None
    libpython: Path | None = None
    cache_tag: str | None = None
    distributions: tuple[DistributionRecord, ...] = ()


@dataclass
class _CopyBudget:
    used: int = 0


@dataclass
class CandidateRuntimeLease:
    """A live-run lease that protects one immutable cache identity."""

    runtime: Path
    lease_path: Path
    cache_root: Path

    @property
    def path(self) -> Path:
        return self.runtime

    @property
    def identity(self) -> str:
        return self.runtime.name

    def __fspath__(self) -> str:
        return os.fspath(self.runtime)

    def __str__(self) -> str:
        return str(self.runtime)

    def __truediv__(self, value: str) -> Path:
        return self.runtime / value

    def __getattr__(self, name: str):
        return getattr(self.runtime, name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CandidateRuntimeLease):
            return self.runtime == other.runtime
        if isinstance(other, (str, Path)):
            return self.runtime == Path(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.runtime)

    def release(self) -> None:
        if not self.cache_root.exists() and not self.cache_root.is_symlink():
            self.lease_path.unlink(missing_ok=True)
            return
        _reject_symlink_components(self.cache_root)
        lock = self.cache_root / ".cache.lock"
        try:
            descriptor = _acquire_lock(lock, self.runtime, self.identity)
        except CandidateRuntimeError:
            raise
        try:
            self.lease_path.unlink(missing_ok=True)
        finally:
            _release_lock(lock, descriptor)

    def _release_locked(self) -> None:
        """Remove this exact lease while the caller already owns the cache lock."""

        self.lease_path.unlink(missing_ok=True)

    def __enter__(self) -> "CandidateRuntimeLease":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


CAD_RUNTIME_ROOTS: tuple[tuple[str, str], ...] = (
    ("build123d", "build123d"),
    ("trimesh", "trimesh"),
    ("OCP", "cadquery-ocp"),
)
CAD_RUNTIME_IMPORTS: tuple[str, ...] = tuple(item[0] for item in CAD_RUNTIME_ROOTS)
CAD_RUNTIME_DISTRIBUTIONS: tuple[str, ...] = tuple(item[1] for item in CAD_RUNTIME_ROOTS)


_PROBE = r'''
import base64, csv, hashlib, io, json, sys, sysconfig
from importlib import metadata
from pathlib import Path
from pathlib import PurePosixPath
from packaging.markers import default_environment
from packaging.requirements import Requirement

def canon(name):
    return name.lower().replace("-", "_").replace(".", "_")

environment = default_environment()
queue = __CAD_RUNTIME_DISTRIBUTIONS__
seen = {}
while queue:
    requested = queue.pop(0)
    key = canon(requested)
    if key in seen:
        continue
    try:
        dist = metadata.distribution(requested)
    except metadata.PackageNotFoundError:
        raise SystemExit("missing fixed CAD distribution: " + requested)
    seen[key] = dist
    for raw in dist.requires or ():
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        queue.append(requirement.name)

records = []
for key in sorted(seen):
    dist = seen[key]
    location = Path(dist.locate_file(""))
    files = []
    for raw in dist.files or ():
        value = raw.as_posix()
        if value.startswith("/"):
            continue
        files.append(value)
    record = dist.read_text("RECORD") or ""
    if not record:
        raise SystemExit("missing distribution RECORD: " + dist.metadata["Name"])
    file_records = []
    seen_record_paths = set()
    def probe_skipped(value):
        path = PurePosixPath(value)
        # These members are generated metadata/cache files which the trusted
        # runtime builder never copies.  They are the only RECORD rows that
        # may omit the wheel RECORD digest/size pair.
        return (
            any(part == "__pycache__" for part in path.parts)
            or any(part.endswith((".dist-info", ".egg-info")) for part in path.parts)
            or path.name in {
                "pyvenv.cfg", "direct_url.json", "RECORD", "INSTALLER",
                "METADATA", "WHEEL", "REQUESTED", "entry_points.txt",
                "top_level.txt",
            }
            or path.suffix.lower() in {".pth", ".egg-link", ".pyc", ".pyo"}
        )
    for row in csv.reader(io.StringIO(record)):
        if len(row) != 3:
            raise SystemExit("invalid distribution RECORD row: " + repr(row))
        if row[0] in seen_record_paths:
            raise SystemExit("duplicate distribution RECORD path: " + row[0])
        seen_record_paths.add(row[0])
        relative = PurePosixPath(row[0])
        if relative.is_absolute() or ".." in relative.parts:
            resolved = (location / relative).resolve()
            if not str(resolved).startswith(str(Path(sys.prefix).resolve()) + "/"):
                raise SystemExit("traversal in distribution RECORD path: " + row[0])
        skipped = probe_skipped(row[0])
        digest = None
        if row[1] and not row[1].startswith("sha256="):
            raise SystemExit("unsupported distribution RECORD algorithm: " + row[0])
        record_path = PurePosixPath(row[0])
        is_record_self = (
            record_path.name == "RECORD"
            and len(record_path.parts) >= 2
            and record_path.parts[-2].endswith(".dist-info")
        )
        if not skipped and not is_record_self and (not row[1] or not row[2]):
            raise SystemExit("unchecked distribution RECORD member: " + row[0])
        if row[1].startswith("sha256="):
            encoded = row[1][len("sha256="):]
            try:
                decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
                if len(decoded) != 32 or encoded != canonical:
                    raise SystemExit("noncanonical distribution RECORD digest: " + row[0])
                digest = decoded.hex()
            except Exception:
                raise SystemExit("invalid distribution RECORD digest: " + row[0])
        try:
            size = int(row[2]) if row[2] else None
        except ValueError:
            raise SystemExit("invalid distribution RECORD size: " + row[0])
        if size is not None and size < 0:
            raise SystemExit("invalid distribution RECORD size: " + row[0])
        if not skipped:
            file_records.append({"path": row[0], "size": size, "sha256": digest})
    if not file_records:
        raise SystemExit("empty distribution RECORD: " + dist.metadata["Name"])
    records.append({
        "name": dist.metadata["Name"],
        "version": dist.version,
        "location": str(location),
        "files": sorted(set(files)),
        "record_sha256": hashlib.sha256(record.encode("utf-8")).hexdigest(),
        "file_records": file_records,
    })

def path_value(name):
    value = sysconfig.get_path(name)
    return str(value) if value else None

libdir = sysconfig.get_config_var("LIBDIR")
ldlibrary = sysconfig.get_config_var("LDLIBRARY")
libpython = str(Path(libdir) / ldlibrary) if libdir and ldlibrary else None
print(json.dumps({
    "version": "%d.%d" % sys.version_info[:2],
    "cache_tag": getattr(sys.implementation, "cache_tag", None),
    "stdlib": path_value("stdlib"),
    "platstdlib": path_value("platstdlib"),
    "purelib": path_value("purelib"),
    "platlib": path_value("platlib"),
    "dynload": sysconfig.get_config_var("DESTSHARED"),
    "interpreter": sys.executable,
    "base_prefix": sys.base_prefix,
    "exec_prefix": sys.exec_prefix,
    "libdir": libdir,
    "libpython": libpython,
    "distributions": records,
}, sort_keys=True))
'''.replace("__CAD_RUNTIME_DISTRIBUTIONS__", repr(list(CAD_RUNTIME_DISTRIBUTIONS)))
_VERSION = re.compile(r"^python(\d+\.\d+)$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_ABSOLUTE = re.compile(rb"(?<![A-Za-z0-9_:/])/(?!/)(?:[^\x00\r\n\t '\"\\\\]+)")
_SKIP_NAMES = frozenset(
    {
        "pyvenv.cfg",
        "direct_url.json",
        "RECORD",
        "INSTALLER",
        "METADATA",
        "WHEEL",
        "REQUESTED",
        "entry_points.txt",
        "top_level.txt",
    }
)
_SKIP_SUFFIXES = frozenset({".pth", ".egg-link", ".pyc", ".pyo"})
_STATIC_LINK_SUFFIXES = frozenset({".a", ".dylib", ".dll", ".lib"})
_COPY_CHUNK = 1024 * 1024
_MAX_RUNTIME_FILE_BYTES = 256 * 1024 * 1024
_MAX_RUNTIME_TREE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_REWRITE_BYTES = 16 * 1024 * 1024
_CACHE_LOCK_SECONDS = 120.0
_MAX_CACHE_ENTRIES = 2
_CACHE_SCHEMA = "mesh-to-cad.agent-runtime-cache/2"
_MANIFEST_NAME = ".runtime-manifest.json"
_MARKER_NAME = ".runtime-complete"
_RECEIPT_NAME = ".runtime-import-receipt.json"
_IMPORT_RECEIPT_VERSION = "1"
_SYSTEM_LIBRARY_ROOTS = (
    Path("/usr/lib"),
    Path("/usr/lib64"),
    Path("/lib"),
    Path("/lib64"),
    Path("/System/Library"),
)


def _resolve_regular(path: Path, *, label: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise CandidateRuntimeError(f"{label}_unavailable") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise CandidateRuntimeError(f"{label}_unavailable")
    return resolved


def _under(path: Path, roots: Iterable[Path]) -> bool:
    resolved = Path(path).resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _open_dir(path: Path, *, dir_fd: int | None = None) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_symlink_escape") from exc


def _reject_symlink_components(path: Path) -> None:
    current = Path(path)
    while not current.exists() and not current.is_symlink() and current.parent != current:
        current = current.parent
    anchor = current
    current = Path(path)
    while True:
        try:
            if current.is_symlink():
                raise CandidateRuntimeError("candidate_runtime_cache_symlink")
        except OSError as exc:
            raise CandidateRuntimeError("candidate_runtime_cache_unavailable") from exc
        if current == anchor or current.parent == current:
            return
        current = current.parent


def _open_relative(root: Path, relative: PurePosixPath) -> int:
    """Open a regular member through no-follow directory descriptors."""

    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise CandidateRuntimeError("candidate_runtime_path_escape")
    descriptor = _open_dir(root)
    try:
        parts = list(relative.parts)
        for part in parts[:-1]:
            child = _open_dir(part, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        result = os.open(parts[-1], flags, dir_fd=descriptor)
        os.close(descriptor)
        return result
    except CandidateRuntimeError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise CandidateRuntimeError("candidate_runtime_symlink_escape") from exc


def _metadata_tuple(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mode,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short candidate runtime write")
        view = view[written:]


def _rewrite_sysconfig_paths(value: bytes) -> bytes:
    """Remove build-machine paths from generated sysconfig metadata."""

    def replacement(match: re.Match[bytes]) -> bytes:
        found = match.group(0)
        return found if found.startswith(b"/runtime") else b"/runtime"

    return _ABSOLUTE.sub(replacement, value)


def _copy_file_stream(
    source_root: Path,
    relative: PurePosixPath,
    target: Path,
    *,
    budget: _CopyBudget,
    rewrite: tuple[tuple[bytes, bytes], ...] = (),
    forbidden: tuple[bytes, ...] = (),
    expected: FileRecord | None = None,
) -> int:
    """Copy one descriptor-bounded file without ``read_bytes`` or symlinks."""

    source_fd = _open_relative(source_root, relative)
    target_created = False
    target_fd: int | None = None
    success = False
    try:
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAX_RUNTIME_FILE_BYTES
            or budget.used + before.st_size > _MAX_RUNTIME_TREE_BYTES
        ):
            raise CandidateRuntimeError("candidate_runtime_file_limit")
        if expected is not None and expected.size is not None and before.st_size != expected.size:
            raise CandidateRuntimeError("candidate_runtime_distribution_drift")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target_fd = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        target_created = True
        first_digest = hashlib.sha256()
        copied = 0
        collected = bytearray() if rewrite else None
        scan_tail = b""
        while copied < before.st_size:
            remaining = before.st_size - copied
            chunk = os.read(source_fd, min(_COPY_CHUNK, remaining))
            if not chunk or len(chunk) > remaining:
                raise CandidateRuntimeError("candidate_runtime_source_changed")
            first_digest.update(chunk)
            copied += len(chunk)
            scanned = scan_tail + chunk
            if not rewrite and any(value and value in scanned for value in forbidden):
                raise CandidateRuntimeError("candidate_runtime_host_path_leak")
            maximum = max((len(value) for value in forbidden), default=1)
            scan_tail = scanned[-(maximum - 1) :] if maximum > 1 else b""
            if collected is not None:
                if len(collected) + len(chunk) > _MAX_REWRITE_BYTES:
                    raise CandidateRuntimeError("candidate_runtime_rewrite_limit")
                collected.extend(chunk)
            else:
                _write_all(target_fd, chunk)
        if collected is not None:
            rewritten = bytes(collected)
            for old, new in rewrite:
                rewritten = rewritten.replace(old, new)
            if relative.name.startswith("_sysconfigdata") or relative.suffix.lower() in {".cfg", ".ini"}:
                rewritten = _rewrite_sysconfig_paths(rewritten)
            if any(value and value in rewritten for value in forbidden):
                raise CandidateRuntimeError("candidate_runtime_host_path_leak")
            if (
                relative.name.startswith("_sysconfigdata")
                or relative.suffix.lower() in {".cfg", ".ini"}
            ):
                for match in _ABSOLUTE.finditer(rewritten):
                    if not match.group(0).startswith(b"/runtime"):
                        raise CandidateRuntimeError("candidate_runtime_host_path_leak")
            if len(rewritten) > _MAX_RUNTIME_FILE_BYTES:
                raise CandidateRuntimeError("candidate_runtime_file_limit")
            _write_all(target_fd, rewritten)
        copied_metadata = os.fstat(source_fd)
        os.lseek(source_fd, 0, os.SEEK_SET)
        second_digest = hashlib.sha256()
        reread = 0
        while reread < before.st_size:
            remaining = before.st_size - reread
            chunk = os.read(source_fd, min(_COPY_CHUNK, remaining))
            if not chunk or len(chunk) > remaining:
                raise CandidateRuntimeError("candidate_runtime_source_changed")
            second_digest.update(chunk)
            reread += len(chunk)
        after = os.fstat(source_fd)
        if (
            copied != before.st_size
            or reread != before.st_size
            or _metadata_tuple(before) != _metadata_tuple(copied_metadata)
            or _metadata_tuple(before) != _metadata_tuple(after)
            or first_digest.digest() != second_digest.digest()
        ):
            raise CandidateRuntimeError("candidate_runtime_source_changed")
        if expected is not None and expected.sha256 and first_digest.hexdigest() != expected.sha256:
            raise CandidateRuntimeError("candidate_runtime_distribution_drift")
        target_size = os.fstat(target_fd).st_size
        if target_size > _MAX_RUNTIME_FILE_BYTES:
            raise CandidateRuntimeError("candidate_runtime_file_limit")
        if not rewrite and target_size != copied:
            raise CandidateRuntimeError("candidate_runtime_target_changed")
        if budget.used + target_size > _MAX_RUNTIME_TREE_BYTES:
            raise CandidateRuntimeError("candidate_runtime_file_limit")
        os.fsync(target_fd)
        budget.used += target_size
        success = True
        return target_size
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_copy_failed") from exc
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if target_created and not success:
            target.unlink(missing_ok=True)
        elif target_created:
            try:
                target.chmod(0o444)
            except OSError:
                pass
        os.close(source_fd)


def _parse_cfg(path: Path) -> Mapping[str, str]:
    values: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return values
    try:
        for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
    except (OSError, UnicodeError):
        return values
    return values


def _probe(python: Path) -> RuntimeProbe | None:
    try:
        completed = subprocess.run(
            [os.fspath(python), "-c", _PROBE],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout.strip())
        records = tuple(
            DistributionRecord(
                str(item["name"]),
                str(item["version"]),
                Path(item["location"]),
                tuple(str(path) for path in item["files"]),
                str(item["record_sha256"]),
                tuple(
                    FileRecord(
                        str(row["path"]),
                        int(row["size"]) if row.get("size") is not None else None,
                        str(row["sha256"]) if row.get("sha256") else None,
                    )
                    for row in item.get("file_records", [])
                ),
            )
            for item in value.get("distributions", [])
        )
        version = str(value["version"])
        stdlib = Path(value["stdlib"])
        platstdlib = Path(value["platstdlib"])
        purelib = Path(value["purelib"]) if value.get("purelib") else None
        platlib = Path(value["platlib"]) if value.get("platlib") else None
        dynload = Path(value["dynload"]) if value.get("dynload") else None
        optional = {
            key: Path(value[key]) if value.get(key) else None
            for key in ("interpreter", "base_prefix", "exec_prefix", "libdir", "libpython")
        }
        cache_tag = str(value["cache_tag"]) if value.get("cache_tag") else None
    except (KeyError, TypeError, ValueError):
        return None
    if not re.fullmatch(r"\d+\.\d+", version):
        return None
    return RuntimeProbe(
        version,
        stdlib,
        platstdlib,
        purelib,
        platlib,
        dynload,
        interpreter=optional["interpreter"],
        base_prefix=optional["base_prefix"],
        exec_prefix=optional["exec_prefix"],
        libdir=optional["libdir"],
        libpython=optional["libpython"],
        cache_tag=cache_tag,
        distributions=records,
    )


def _version_from_tree(venv: Path, cfg: Mapping[str, str]) -> str | None:
    match = re.search(r"(\d+\.\d+)", cfg.get("version", ""))
    if match:
        return match.group(1)
    lib = venv / "lib"
    if not lib.is_dir():
        return None
    versions = sorted(
        match.group(1)
        for child in lib.iterdir()
        if child.is_dir() and (match := _VERSION.match(child.name))
    )
    return versions[0] if versions else None


def _candidate_stdlib(venv: Path, probe: RuntimeProbe, version: str) -> Path:
    options = [probe.stdlib, probe.platstdlib]
    cfg = _parse_cfg(venv / "pyvenv.cfg")
    if cfg.get("home"):
        home = Path(cfg["home"])
        options.extend(
            (home / "lib" / f"python{version}", home.parent / "lib" / f"python{version}")
        )
    options.append(venv / "lib" / f"python{version}")
    for option in options:
        try:
            resolved = option.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and (resolved / "os.py").is_file():
            return resolved
    raise CandidateRuntimeError("candidate_runtime_stdlib_unavailable")


def _normalize_relative(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _skip_member(relative: PurePosixPath) -> bool:
    return (
        any(part == "__pycache__" for part in relative.parts)
        or relative.name in _SKIP_NAMES
        or relative.suffix.lower() in _SKIP_SUFFIXES
        or any(part.endswith((".dist-info", ".egg-info")) for part in relative.parts)
    )


def _walk_tree(root: Path) -> Iterable[PurePosixPath]:
    """Yield regular members through descriptor-backed no-follow traversal."""

    root_fd = _open_dir(root)

    def visit(directory_fd: int, relative: PurePosixPath) -> Iterable[PurePosixPath]:
        try:
            entries = os.scandir(directory_fd)
        except OSError as exc:
            raise CandidateRuntimeError("candidate_runtime_source_unavailable") from exc
        with entries:
            for entry in sorted(entries, key=lambda item: item.name):
                child = relative / entry.name
                if _skip_member(child):
                    continue
                if entry.is_symlink():
                    if entry.name == "site-packages" or entry.suffix.lower() in _STATIC_LINK_SUFFIXES:
                        continue
                    raise CandidateRuntimeError("candidate_runtime_symlink_escape")
                if entry.is_dir(follow_symlinks=False):
                    child_fd = _open_dir(entry.name, dir_fd=directory_fd)
                    try:
                        yield from visit(child_fd, child)
                    finally:
                        os.close(child_fd)
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise CandidateRuntimeError("candidate_runtime_source_unavailable") from exc
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise CandidateRuntimeError("candidate_runtime_special_file")
                yield child

    try:
        yield from visit(root_fd, PurePosixPath())
    finally:
        os.close(root_fd)


def _parse_tool_dependencies(
    path: Path,
    roots: tuple[Path, ...],
) -> tuple[tuple[str, Path], ...]:
    """Resolve only non-system loader dependencies of one native file."""

    if platform.system() == "Darwin":
        tool = shutil.which("otool")
        if not tool:
            raise CandidateRuntimeError("candidate_runtime_loader_unavailable")
        command = [tool, "-L", os.fspath(path)]
    elif platform.system() == "Linux":
        tool = shutil.which("ldd")
        if not tool:
            raise CandidateRuntimeError("candidate_runtime_loader_unavailable")
        command = [tool, os.fspath(path)]
    else:
        return ()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateRuntimeError("candidate_runtime_loader_unavailable") from exc
    if completed.returncode != 0:
        raise CandidateRuntimeError("candidate_runtime_loader_unavailable")
    rpaths: list[Path] = []
    if platform.system() == "Darwin":
        lines = completed.stdout.splitlines()
        for index, line in enumerate(lines):
            if "cmd LC_RPATH" not in line:
                continue
            for candidate_line in lines[index + 1 : index + 5]:
                value = candidate_line.strip()
                if not value.startswith("path "):
                    continue
                raw_path = value.removeprefix("path ").split(" ", 1)[0]
                if raw_path.startswith("@loader_path/"):
                    rpaths.append(path.parent / raw_path.removeprefix("@loader_path/"))
                elif raw_path.startswith("@executable_path/"):
                    rpaths.append(path.parent / raw_path.removeprefix("@executable_path/"))
                else:
                    rpaths.append(Path(raw_path))
                break
    dependencies: list[tuple[str, Path]] = []
    for line in completed.stdout.splitlines()[1:]:
        stripped = line.strip()
        if "=>" in stripped:
            raw = stripped.split("=>", 1)[1].strip().split(" ", 1)[0]
        else:
            raw = stripped.split(" ", 1)[0]
        if not raw or raw in {"linux-vdso.so.1", "statically"} or raw.startswith("/DLC/"):
            continue
        if raw.startswith("@loader_path/"):
            candidate = path.parent / raw.removeprefix("@loader_path/")
        elif raw.startswith("@executable_path/"):
            candidate = path.parent / raw.removeprefix("@executable_path/")
        elif raw.startswith("@rpath/"):
            name = raw.removeprefix("@rpath/")
            candidates = [
                *(root / name for root in rpaths),
                path.parent / name,
                path.parent / ".dylibs" / name,
                *(root / name for root in roots),
            ]
            candidate = next((item for item in candidates if item.exists()), None)
            if candidate is None:
                for root in roots:
                    try:
                        candidate = next(root.rglob(name))
                        break
                    except StopIteration:
                        continue
            if candidate is None and "." in name:
                stem = name.split(".", 1)[0]
                for root in roots:
                    matches = sorted(
                        item
                        for item in root.rglob(f"{stem}*")
                        if item.is_file() and item.name.startswith(stem)
                    )
                    if matches:
                        candidate = matches[0]
                        break
            if candidate is None:
                candidate = candidates[0]
        else:
            candidate = Path(raw)
        if any(
            raw == os.fspath(root) or raw.startswith(os.fspath(root) + "/")
            for root in _SYSTEM_LIBRARY_ROOTS
        ):
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            # A plain @rpath is resolved by the package's own loader search;
            # absolute unresolved paths cannot be relocated safely.
            if raw.startswith("@"):
                raise CandidateRuntimeError("candidate_runtime_loader_unavailable")
            raise CandidateRuntimeError("candidate_runtime_loader_unavailable")
        if _under(resolved, _SYSTEM_LIBRARY_ROOTS):
            continue
        if not _under(resolved, roots):
            raise CandidateRuntimeError("candidate_runtime_loader_escape")
        dependencies.append((raw, resolved))
    return tuple(dict.fromkeys(dependencies))


def _relocate_libpython(path: Path, forbidden: tuple[bytes, ...]) -> None:
    """Rewrite the standalone Python dylib identity to the stable mount."""

    if platform.system() == "Darwin":
        tool = shutil.which("install_name_tool")
        if not tool:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable")
        try:
            path.chmod(0o755)
            completed = subprocess.run(
                [tool, "-id", f"/runtime/lib/{path.name}", os.fspath(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable") from exc
        if completed.returncode != 0:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable")
        _adhoc_sign(path)
        path.chmod(0o555)
    try:
        with path.open("rb") as stream:
            tail = b""
            maximum = max((len(value) for value in forbidden), default=1)
            while chunk := stream.read(_COPY_CHUNK):
                scanned = tail + chunk
                if any(value and value in scanned for value in forbidden):
                    raise CandidateRuntimeError("candidate_runtime_host_path_leak")
                tail = scanned[-(maximum - 1) :] if maximum > 1 else b""
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_source_unavailable") from exc


def _relocate_native(path: Path, stable_id: str, forbidden: tuple[bytes, ...]) -> None:
    """Rewrite bundled dylib identities and reject source-path remnants."""

    if platform.system() == "Darwin" and path.suffix.lower() == ".dylib":
        tool = shutil.which("install_name_tool")
        if not tool:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable")
        try:
            path.chmod(0o755)
            completed = subprocess.run(
                [tool, "-id", stable_id, os.fspath(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable") from exc
        if completed.returncode != 0:
            raise CandidateRuntimeError("candidate_runtime_relocation_unavailable")
        _adhoc_sign(path)
        path.chmod(0o555)
    try:
        with path.open("rb") as stream:
            tail = b""
            maximum = max((len(value) for value in forbidden), default=1)
            while chunk := stream.read(_COPY_CHUNK):
                scanned = tail + chunk
                if b"/DLC/" in scanned or any(value and value in scanned for value in forbidden):
                    raise CandidateRuntimeError("candidate_runtime_host_path_leak")
                tail = scanned[-(maximum - 1) :] if maximum > 1 else b""
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_source_unavailable") from exc


def _adhoc_sign(path: Path) -> None:
    """Restore an executable ad-hoc signature after Mach-O relocation."""

    tool = shutil.which("codesign")
    if not tool:
        raise CandidateRuntimeError("candidate_runtime_signing_unavailable")
    try:
        completed = subprocess.run(
            [tool, "--force", "--sign", "-", os.fspath(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateRuntimeError("candidate_runtime_signing_unavailable") from exc
    if completed.returncode != 0:
        raise CandidateRuntimeError("candidate_runtime_signing_unavailable")


def _digest_small(path: Path) -> str:
    metadata = path.stat()
    if metadata.st_size > 64 * 1024 * 1024:
        return f"stat:{metadata.st_size}:{metadata.st_mtime_ns}:{metadata.st_ino}"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _stdlib_identity(stdlib: Path) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    candidates = [stdlib / "os.py", stdlib / "importlib/__init__.py"]
    candidates.extend(sorted(stdlib.glob("_sysconfigdata*.py")))
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            facts.append((path.relative_to(stdlib).as_posix(), _digest_small(path)))
    return facts


def _cache_identity(venv: Path, probe: RuntimeProbe, interpreter: Path) -> str:
    cfg = _parse_cfg(venv / "pyvenv.cfg")
    normalized_cfg = {
        key: value
        for key, value in sorted(cfg.items())
        if key not in {"home", "executable", "command"}
    }
    value = {
        "schema": _CACHE_SCHEMA,
        "platform": [platform.system(), platform.machine(), platform.python_implementation()],
        "python": [probe.version, probe.cache_tag],
        "stdlib": _stdlib_identity(probe.stdlib),
        "cfg": normalized_cfg,
        "interpreter": _digest_small(interpreter),
        "libpython": _digest_small(probe.libpython) if probe.libpython and probe.libpython.is_file() else None,
        "distributions": [
            [item.name.lower(), item.version, item.record_sha256, len(item.files)]
            for item in probe.distributions
        ],
    }
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(_CACHE_SCHEMA.encode() + b"\0" + body).hexdigest()


def _hash_cached_file(root: Path, relative: PurePosixPath) -> tuple[int, str]:
    descriptor = _open_relative(root, relative)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
        digest = hashlib.sha256()
        copied = 0
        while copied < before.st_size:
            remaining = before.st_size - copied
            chunk = os.read(descriptor, min(_COPY_CHUNK, remaining))
            if not chunk or len(chunk) > remaining:
                raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
            digest.update(chunk)
            copied += len(chunk)
        after = os.fstat(descriptor)
        if copied != before.st_size or _metadata_tuple(before) != _metadata_tuple(after):
            raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
        return copied, digest.hexdigest()
    finally:
        os.close(descriptor)


def _manifest_files(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.name in {_MANIFEST_NAME, _MARKER_NAME, _RECEIPT_NAME}:
            if path.is_symlink():
                raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
            if path.parent != root:
                raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
            continue
        if path.is_symlink():
            raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
        if path.is_dir():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if _normalize_relative(relative.as_posix()) is None:
            raise CandidateRuntimeError("candidate_runtime_manifest_invalid")
        size, digest = _hash_cached_file(root, relative)
        total += size
        if total > _MAX_RUNTIME_TREE_BYTES:
            raise CandidateRuntimeError("candidate_runtime_manifest_limit")
        files.append({"path": relative.as_posix(), "size_bytes": size, "sha256": digest})
    return files


def _manifest_bytes(identity: str, files: list[dict[str, object]]) -> bytes:
    total = sum(int(item["size_bytes"]) for item in files)
    payload = {
        "schema": _CACHE_SCHEMA,
        "identity": identity,
        "total_bytes": total,
        "files": files,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _write_manifest(root: Path, identity: str) -> str:
    content = _manifest_bytes(identity, _manifest_files(root))
    target = root / _MANIFEST_NAME
    target.write_bytes(content)
    target.chmod(0o444)
    return hashlib.sha256(content).hexdigest()


def _write_import_receipt(root: Path, identity: str, manifest_sha256: str, imports: tuple[str, ...]) -> str:
    content = (
        json.dumps(
            {
                "schema": _CACHE_SCHEMA,
                "version": _IMPORT_RECEIPT_VERSION,
                "identity": identity,
                "manifest_sha256": manifest_sha256,
                "imports": list(imports),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    target = root / _RECEIPT_NAME
    target.write_bytes(content)
    target.chmod(0o444)
    return hashlib.sha256(content).hexdigest()


def _validate_import_receipt(
    root: Path,
    identity: str,
    manifest_sha256: str,
    receipt_sha256: str,
) -> bool:
    try:
        target = root / _RECEIPT_NAME
        if target.is_symlink():
            return False
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != receipt_sha256:
            return False
        value = json.loads(content.decode("ascii"))
        return (
            set(value) == {"schema", "version", "identity", "manifest_sha256", "imports"}
            and value.get("schema") == _CACHE_SCHEMA
            and value.get("version") == _IMPORT_RECEIPT_VERSION
            and value.get("identity") == identity
            and value.get("manifest_sha256") == manifest_sha256
            and value.get("imports") == list(CAD_RUNTIME_IMPORTS)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return False


def _validate_manifest(root: Path, identity: str, expected_sha256: str) -> bool:
    try:
        manifest_path = root / _MANIFEST_NAME
        content = manifest_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            return False
        value = json.loads(content.decode("ascii"))
        if set(value) != {"schema", "identity", "total_bytes", "files"}:
            return False
        if value["schema"] != _CACHE_SCHEMA or value["identity"] != identity:
            return False
        files = value["files"]
        if not isinstance(files, list) or files != sorted(files, key=lambda item: item.get("path", "")):
            return False
        expected: dict[str, tuple[int, str]] = {}
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "size_bytes", "sha256"}:
                return False
            relative = _normalize_relative(item["path"])
            if relative is None or relative.name in {_MANIFEST_NAME, _MARKER_NAME, _RECEIPT_NAME}:
                return False
            if relative.as_posix() in expected or not isinstance(item["size_bytes"], int):
                return False
            if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                return False
            expected[relative.as_posix()] = (item["size_bytes"], item["sha256"])
        actual = {item["path"]: (int(item["size_bytes"]), str(item["sha256"])) for item in _manifest_files(root)}
        return (
            actual == expected
            and int(value["total_bytes"]) == sum(size for size, _digest in actual.values())
            and int(value["total_bytes"]) <= _MAX_RUNTIME_TREE_BYTES
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, AttributeError, CandidateRuntimeError):
        return False


def _is_complete(runtime: Path, identity: str) -> bool:
    marker = runtime / _MARKER_NAME
    if marker.is_symlink():
        return False
    try:
        value = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    try:
        return (
            set(value) == {"schema", "identity", "manifest_sha256", "receipt_sha256"}
            and value["schema"] == _CACHE_SCHEMA
            and value["identity"] == identity
            and isinstance(value["manifest_sha256"], str)
            and isinstance(value["receipt_sha256"], str)
            and not (runtime.stat().st_mode & 0o222)
            and (runtime / "bin/python").is_file()
            and any(
                path.is_dir() and _VERSION.fullmatch(path.name)
                for path in (runtime / "lib").glob("python*")
            )
            and _validate_manifest(runtime, identity, value["manifest_sha256"])
            and _validate_import_receipt(
                runtime,
                identity,
                value["manifest_sha256"],
                value["receipt_sha256"],
            )
        )
    except OSError:
        return False


def _make_read_only(root: Path, *, root_read_only: bool = True) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise CandidateRuntimeError("candidate_runtime_symlink_escape")
        try:
            if path.is_dir():
                path.chmod(0o555)
            else:
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        except OSError as exc:
            raise CandidateRuntimeError("candidate_runtime_publish_failed") from exc
    if root_read_only:
        root.chmod(0o555)


def _remove_cache_tree(root: Path) -> None:
    """Remove one exact cache entry after making its immutable bytes writable."""

    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink():
        root.unlink()
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass
    try:
        root.chmod(0o755)
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=False)


def _cleanup_orphan_temps(cache_root: Path) -> None:
    for path in cache_root.glob(".*.tmp-*"):
        if path.is_dir() or path.is_symlink():
            _remove_cache_tree(path)


def _process_start_token(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        if proc_stat.is_file():
            value = proc_stat.read_text(encoding="ascii")
            tail = value[value.rfind(")") + 2 :].split()
            return tail[19] if len(tail) > 19 else None
    except (OSError, UnicodeError, IndexError):
        return None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            value = result.stdout.strip()
            return value or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _boot_token() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if value:
            return value
    except (OSError, UnicodeError):
        pass
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            value = result.stdout.strip()
            return value or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _owner_record() -> dict[str, object]:
    pid = os.getpid()
    return {
        "pid": pid,
        "host": socket.gethostname(),
        "boot": _boot_token(),
        "start": _process_start_token(pid),
        "created_ns": time.time_ns(),
    }


def _owner_live(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    pid = value.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    if value.get("host") != socket.gethostname():
        return False
    current_boot = _boot_token()
    if value.get("boot") != current_boot:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    recorded_start = value.get("start")
    current_start = _process_start_token(pid)
    return bool(recorded_start and current_start and recorded_start == current_start)


def _write_owner(descriptor: int, identity: str | None = None) -> None:
    owner = _owner_record()
    if identity is not None:
        owner["identity"] = identity
    payload = (json.dumps(owner, sort_keys=True) + "\n").encode("ascii")
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, payload)
    os.fsync(descriptor)


def _read_owner(lock: Path) -> object:
    try:
        return json.loads(lock.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _read_owner_fd(descriptor: int) -> object:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        return json.loads(os.read(descriptor, 8192).decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _flock(descriptor: int, *, blocking: bool) -> bool:
    if fcntl is None:
        return True
    flags = fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(descriptor, flags)
        return True
    except BlockingIOError:
        return False


def _acquire_lock(lock: Path, final: Path, identity: str) -> int:
    deadline = time.monotonic() + _CACHE_LOCK_SECONDS
    while True:
        try:
            descriptor = os.open(
                lock,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            if not _flock(descriptor, blocking=False):
                os.close(descriptor)
                continue
            _write_owner(descriptor, identity)
            return descriptor
        except FileExistsError:
            try:
                descriptor = os.open(
                    lock,
                    os.O_RDWR
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise CandidateRuntimeError("candidate_runtime_cache_lock_failed") from exc
            acquired = _flock(descriptor, blocking=False)
            if not acquired:
                os.close(descriptor)
            elif _owner_live(_read_owner_fd(descriptor)):
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            else:
                _write_owner(descriptor, identity)
                return descriptor
            if time.monotonic() >= deadline:
                raise CandidateRuntimeError("candidate_runtime_cache_lock_timeout")
            time.sleep(0.05)
        except OSError as exc:
            raise CandidateRuntimeError("candidate_runtime_cache_lock_failed") from exc


def _release_lock(lock: Path, descriptor: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _lease_records(cache_root: Path, identity: str) -> list[Path]:
    directory = cache_root / "leases" / identity
    if directory.is_symlink():
        raise CandidateRuntimeError("candidate_runtime_cache_symlink")
    if not directory.is_dir():
        return []
    live: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file():
            if path.is_dir() and not path.is_symlink():
                _remove_cache_tree(path)
            else:
                path.unlink(missing_ok=True)
            continue
        if _owner_live(_read_owner(path)):
            live.append(path)
        else:
            path.unlink(missing_ok=True)
    return live


def _prune_cache(cache_root: Path, protected: str) -> None:
    entries = [
        path
        for path in cache_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and re.fullmatch(r"[0-9a-f]{64}", path.name)
        and (path.name == protected or _is_complete(path, path.name))
    ]
    for path in entries:
        _lease_records(cache_root, path.name)
    if len(entries) <= _MAX_CACHE_ENTRIES:
        return
    entries.sort(key=lambda item: item.stat().st_mtime_ns)
    live = {protected}
    for path in entries:
        if _lease_records(cache_root, path.name):
            live.add(path.name)
    keep = set(live)
    for path in reversed(entries):
        if len(keep) >= _MAX_CACHE_ENTRIES:
            break
        keep.add(path.name)
    for path in entries:
        if path.name in keep:
            continue
        _remove_cache_tree(path)


def _create_runtime_lease(
    cache_root: Path,
    runtime: Path,
    identity: str,
) -> CandidateRuntimeLease:
    """Create a lease while the caller owns the global cache lock."""

    lease_root = Path(cache_root) / "leases" / identity
    try:
        _reject_symlink_components(lease_root)
        lease_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lease_root.chmod(0o700)
        lease = lease_root / f"{os.getpid()}-{secrets.token_hex(8)}.json"
        descriptor = os.open(
            lease,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            payload = {"schema": _CACHE_SCHEMA, "identity": identity, **_owner_record()}
            os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_lease_failed") from exc
    return CandidateRuntimeLease(runtime, lease, Path(cache_root))


def _rewrite_map(
    venv: Path,
    probe: RuntimeProbe,
    stdlib: Path,
    site_roots: tuple[Path, ...],
) -> tuple[tuple[bytes, bytes], ...]:
    pairs: dict[str, str] = {}

    def add(source: Path | None, target: str) -> None:
        if source is None:
            return
        pairs[str(source)] = target
        try:
            pairs[str(source.resolve())] = target
        except OSError:
            pass

    add(stdlib, f"/runtime/lib/python{probe.version}")
    add(probe.stdlib, f"/runtime/lib/python{probe.version}")
    add(probe.platstdlib, f"/runtime/lib/python{probe.version}")
    add(probe.dynload, f"/runtime/lib/python{probe.version}/lib-dynload")
    add(venv, "/runtime")
    for site in site_roots:
        add(site, f"/runtime/lib/python{probe.version}/site-packages")
    add(probe.purelib, f"/runtime/lib/python{probe.version}/site-packages")
    add(probe.platlib, f"/runtime/lib/python{probe.version}/site-packages")
    for source in (probe.base_prefix, probe.exec_prefix, probe.libdir, probe.interpreter.parent if probe.interpreter else None):
        add(source, "/runtime")
    return tuple(
        (old.encode("utf-8"), new.encode("utf-8"))
        for old, new in sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)
        if old and new
    )


def _build_runtime(
    target: Path,
    venv: Path,
    probe: RuntimeProbe,
    interpreter: Path,
    stdlib: Path,
    site_roots: tuple[Path, ...],
    identity: str,
    repo_root: Path | None,
) -> tuple[str, str]:
    target.mkdir(mode=0o700)
    (target / "bin").mkdir(mode=0o755)
    runtime_lib = target / "lib" / f"python{probe.version}"
    runtime_lib.mkdir(parents=True, mode=0o755)
    budget = _CopyBudget()
    rewrite = _rewrite_map(venv, probe, stdlib, site_roots)
    # A system prefix is a valid runtime string (for example in stdlib data
    # files), not evidence that the source checkout leaked.  More specific
    # source paths remain forbidden, while sysconfig files still rewrite every
    # entry in ``rewrite`` below.
    forbidden_values = [
        old for old, _new in rewrite if old.rstrip(b"/") not in {b"", b"/usr"}
    ]
    if repo_root is not None:
        forbidden_values.append(os.fspath(repo_root).encode("utf-8"))
    forbidden = tuple(dict.fromkeys(forbidden_values))
    copied: set[PurePosixPath] = set()
    for relative in _walk_tree(stdlib):
        if relative in copied:
            continue
        copied.add(relative)
        _copy_file_stream(
            stdlib,
            relative,
            runtime_lib / relative,
            budget=budget,
            rewrite=rewrite if relative.name.startswith("_sysconfigdata") else (),
            forbidden=forbidden,
        )
    package_root = runtime_lib / "site-packages"
    allowed_roots = (venv, stdlib, *site_roots)
    native_files: list[Path] = []
    native_targets: dict[Path, Path] = {}
    for record in probe.distributions:
        location = record.location.resolve()
        if not _under(location, allowed_roots):
            raise CandidateRuntimeError("candidate_runtime_distribution_escape")
        expected_records = {
            item.path: item
            for item in record.file_records
        }
        if record.file_records:
            listed = set(record.files)
            for item in record.file_records:
                if item.path not in listed and PurePosixPath(item.path).name != "RECORD":
                    raise CandidateRuntimeError("candidate_runtime_distribution_drift")
        for raw in record.files:
            relative = _normalize_relative(raw)
            if relative is None:
                candidate = (location / PurePosixPath(raw)).resolve()
                if ".." in PurePosixPath(raw).parts and _under(candidate, (venv,)):
                    continue
                raise CandidateRuntimeError("candidate_runtime_distribution_escape")
            if _skip_member(relative):
                continue
            source = location / relative
            try:
                source.resolve(strict=True).relative_to(location)
            except (OSError, ValueError) as exc:
                raise CandidateRuntimeError("candidate_runtime_distribution_escape") from exc
            package_target = package_root / relative
            if relative in copied:
                continue
            if record.file_records and relative.as_posix() not in expected_records:
                if relative.name == "RECORD":
                    continue
                raise CandidateRuntimeError("candidate_runtime_distribution_drift")
            copied.add(relative)
            _copy_file_stream(
                location,
                relative,
                package_target,
                budget=budget,
                rewrite=rewrite if relative.suffix.lower() in {".cfg", ".ini"} else (),
                forbidden=forbidden,
                expected=expected_records.get(relative.as_posix()),
            )
            if package_target.suffix.lower() in {".so", ".dylib", ".dll", ".pyd"}:
                _relocate_native(
                    package_target,
                    f"/runtime/lib/python{probe.version}/site-packages/{relative.as_posix()}",
                    forbidden,
                )
                native_source = source.resolve()
                native_files.append(native_source)
                native_targets[native_source] = package_target
        metadata_dir = next(
            (
                part
                for raw in record.files
                for part in PurePosixPath(raw).parts
                if part.endswith(".dist-info")
            ),
            f"{record.name.replace('-', '_')}-{record.version}.dist-info",
        )
        metadata_target = package_root / metadata_dir
        metadata_target.mkdir(parents=True, exist_ok=True, mode=0o755)
        (metadata_target / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {record.name}\nVersion: {record.version}\n",
            encoding="ascii",
        )
        (metadata_target / "WHEEL").write_text(
            "Wheel-Version: 1.0\nGenerator: mesh-to-cad-runtime\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            encoding="ascii",
        )
    if probe.libpython and probe.libpython.is_file():
        libpython = _resolve_regular(probe.libpython, label="candidate_runtime_libpython")
        if not _under(libpython, (venv, stdlib, *site_roots, probe.libdir or libpython.parent)):
            raise CandidateRuntimeError("candidate_runtime_loader_escape")
        destination = target / "lib" / libpython.name
        relative_root = libpython.parent
        _copy_file_stream(
            relative_root,
            PurePosixPath(libpython.name),
            destination,
            budget=budget,
            forbidden=(),
        )
        _relocate_libpython(destination, forbidden)
        native_files.append(libpython)
        native_targets[libpython] = destination
    python_destination = target / "bin/python"
    interpreter_root = interpreter.parent
    _copy_file_stream(
        interpreter_root,
        PurePosixPath(interpreter.name),
        python_destination,
        budget=budget,
        forbidden=forbidden,
    )
    native_files.append(interpreter)
    native_targets[interpreter] = python_destination
    python_destination.chmod(0o555)
    loader_roots = tuple(dict.fromkeys((venv, stdlib, *site_roots, probe.libdir or interpreter_root)))
    for native in native_files:
        for raw, dependency in _parse_tool_dependencies(native, loader_roots):
            if not raw.startswith("@rpath/"):
                continue
            name = PurePosixPath(raw.removeprefix("@rpath/")).name
            dependency_target = native_targets.get(dependency)
            if dependency_target is None:
                raise CandidateRuntimeError("candidate_runtime_loader_escape")
            alias = dependency_target.parent / name
            if alias.exists():
                continue
            _copy_file_stream(
                dependency.parent,
                PurePosixPath(dependency.name),
                alias,
                budget=budget,
                forbidden=forbidden,
            )
            if alias.suffix.lower() == ".dylib":
                _relocate_native(alias, f"/runtime/lib/python{probe.version}/site-packages/{alias.relative_to(target / 'lib' / f'python{probe.version}' / 'site-packages').as_posix()}", forbidden)
    manifest_sha256 = _write_manifest(target, identity)
    imports = CAD_RUNTIME_IMPORTS
    validate_candidate_runtime(target, imports)
    receipt_sha256 = _write_import_receipt(target, identity, manifest_sha256, imports)
    return manifest_sha256, receipt_sha256


def materialize_candidate_runtime(
    source_venv: Path,
    cache_root: Path,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Return the shared immutable runtime for the fixed CAD operation."""

    raw_venv = Path(source_venv)
    if raw_venv.is_symlink() or not raw_venv.is_dir():
        raise CandidateRuntimeError("candidate_runtime_unavailable")
    venv = raw_venv.resolve()
    raw_cache = Path(cache_root)
    _reject_symlink_components(raw_cache)
    try:
        raw_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_cache.chmod(0o700)
    except OSError as exc:
        raise CandidateRuntimeError("candidate_runtime_cache_unavailable") from exc
    python_entry = next(
        (venv / "bin" / name for name in ("python", "python3", "python3.12") if (venv / "bin" / name).exists()),
        None,
    )
    if python_entry is None:
        raise CandidateRuntimeError("candidate_runtime_unavailable")
    interpreter = _resolve_regular(python_entry, label="candidate_runtime_interpreter")
    if not os.access(interpreter, os.X_OK):
        raise CandidateRuntimeError("candidate_runtime_interpreter_unavailable")
    # Probe through the venv entrypoint so its site-packages and editable
    # path setup are visible; copy the resolved standalone interpreter below.
    probe = _probe(python_entry)
    if probe is None:
        raise CandidateRuntimeError("candidate_runtime_probe_unavailable")
    version = probe.version or _version_from_tree(venv, _parse_cfg(venv / "pyvenv.cfg"))
    stdlib = _candidate_stdlib(venv, probe, version)
    site_roots = tuple(
        root.resolve()
        for root in (probe.purelib, probe.platlib)
        if root and root.is_dir() and _under(root, (venv, stdlib))
    )
    identity = _cache_identity(venv, probe, interpreter)
    final = raw_cache / identity
    if final.is_symlink():
        raise CandidateRuntimeError("candidate_runtime_cache_corrupt")
    lock = raw_cache / ".cache.lock"
    lock_fd = _acquire_lock(lock, final, identity)
    temporary = raw_cache / f".{identity}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    lease: CandidateRuntimeLease | None = None
    try:
        _cleanup_orphan_temps(raw_cache)
        if _is_complete(final, identity):
            lease = _create_runtime_lease(raw_cache, final, identity)
            _prune_cache(raw_cache, identity)
            return lease
        if final.is_symlink():
            raise CandidateRuntimeError("candidate_runtime_cache_corrupt")
        if final.exists():
            _remove_cache_tree(final)
        manifest_sha256, receipt_sha256 = _build_runtime(
            temporary,
            venv,
            probe,
            interpreter,
            stdlib,
            site_roots,
            identity,
            Path(repo_root).resolve() if repo_root is not None else None,
        )
        _make_read_only(temporary, root_read_only=False)
        try:
            os.replace(temporary, final)
        except FileExistsError:
            _remove_cache_tree(temporary)
        else:
            marker = final / _MARKER_NAME
            marker.write_text(
                json.dumps(
                    {
                        "schema": _CACHE_SCHEMA,
                        "identity": identity,
                        "manifest_sha256": manifest_sha256,
                        "receipt_sha256": receipt_sha256,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="ascii",
            )
            marker.chmod(0o444)
            final.chmod(0o555)
        if not _is_complete(final, identity):
            raise CandidateRuntimeError("candidate_runtime_publish_failed")
        lease = _create_runtime_lease(raw_cache, final, identity)
        _prune_cache(raw_cache, identity)
        return lease
    except Exception:
        if lease is not None:
            lease._release_locked()
        _remove_cache_tree(temporary)
        raise
    finally:
        _release_lock(lock, lock_fd)


def validate_candidate_runtime(
    runtime: Path,
    required_imports: Iterable[str] = CAD_RUNTIME_IMPORTS,
) -> None:
    """Launch the immutable view and validate the fixed CAD imports."""

    modules = tuple(required_imports)
    if any(not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) for name in modules):
        raise CandidateRuntimeError("candidate_runtime_import_contract")
    python = _resolve_regular(Path(runtime) / "bin/python", label="candidate_runtime_interpreter")
    versions = sorted(
        path.name
        for path in (Path(runtime) / "lib").glob("python*")
        if path.is_dir() and _VERSION.fullmatch(path.name)
    )
    if not versions:
        raise CandidateRuntimeError("candidate_runtime_stdlib_unavailable")
    site = Path(runtime) / "lib" / versions[0] / "site-packages"
    script = "import " + ", ".join(modules) if modules else "import os"
    try:
        completed = subprocess.run(
            [os.fspath(python), "-c", script],
            cwd=runtime,
            env={
                "PATH": "/runtime/bin",
                "PYTHONHOME": os.fspath(runtime),
                "PYTHONPATH": os.fspath(site),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "LC_ALL": "C",
            },
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateRuntimeError("candidate_runtime_import_failed") from exc
    if completed.returncode != 0:
        raise CandidateRuntimeError("candidate_runtime_import_failed")


__all__ = ["CandidateRuntimeError"]
