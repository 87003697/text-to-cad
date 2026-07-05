from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cadgen.catalog import (
    cad_ref_from_step_path as fallback_cad_ref_from_step_path,
    find_source_by_cad_ref,
    find_source_by_path,
)


def _source_for_step_path(step_path: Path):
    source = find_source_by_path(step_path)
    if source is not None:
        return source
    cad_ref = fallback_cad_ref_from_step_path(step_path)
    source = find_source_by_cad_ref(cad_ref)
    if source is None:
        raise ValueError(f"CAD STEP ref not found: {cad_ref}")
    return source


def part_stl_path(step_path: Path) -> Path:
    source = _source_for_step_path(step_path)
    if source.stl_path is None:
        raise ValueError(f"CAD STEP ref has no configured STL output: {source.cad_ref}")
    return source.stl_path


def part_3mf_path(step_path: Path) -> Path:
    source = _source_for_step_path(step_path)
    if source.three_mf_path is None:
        raise ValueError(f"CAD STEP ref has no configured 3MF output: {source.cad_ref}")
    return source.three_mf_path


def part_native_glb_path(step_path: Path) -> Path:
    source = _source_for_step_path(step_path)
    if source.native_glb_path is None:
        raise ValueError(f"CAD STEP ref has no configured native GLB output: {source.cad_ref}")
    return source.native_glb_path


def relative_to_cwd(path: Path) -> str:
    # Display/label + CLI-payload helper (the payload packagePath/stepPath are overwritten by the
    # viewer; the persisted descriptor's model-folder-relative paths come from relative_to_file,
    # not this). Anchored on the live cwd, not a frozen import-time root.
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def relative_to_directory(path: Path, base_dir: Path) -> str:
    return os.path.relpath(
        path.expanduser().resolve(),
        start=base_dir.expanduser().resolve(),
    ).replace(os.sep, "/")


def relative_to_file(path: Path, owner_path: Path) -> str:
    return relative_to_directory(path, owner_path.expanduser().resolve().parent)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
