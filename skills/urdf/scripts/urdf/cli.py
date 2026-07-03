from __future__ import annotations

import argparse
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(SCRIPTS_DIR))

from urdf.source import UrdfSource, UrdfSourceError, UrdfSourceWarning, read_urdf_source

URDF_SUFFIX = ".urdf"


def validate_urdf_targets(targets: Sequence[str]) -> int:
    target_paths = [_resolve_target_path(target) for target in targets]
    failed = False
    for target_path in target_paths:
        if not _validate_target(target_path):
            failed = True
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="urdf",
        description="Validate explicit URDF targets.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Explicit .urdf file to validate.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return validate_urdf_targets(args.targets)


def _resolve_target_path(raw_target: object) -> Path:
    value = str(raw_target or "").strip()
    if not value:
        raise ValueError("urdf target must be a non-empty path")
    target_path = Path(value).expanduser()
    return target_path.resolve() if target_path.is_absolute() else (Path.cwd() / target_path).resolve()


def _validate_target(target_path: Path) -> bool:
    display = _display_path(target_path)
    if target_path.suffix.lower() != URDF_SUFFIX:
        print(f"FAIL {display}: target must be a .urdf file", file=sys.stderr)
        return False
    if not target_path.is_file():
        print(f"FAIL {display}: file not found", file=sys.stderr)
        return False
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", UrdfSourceWarning)
        try:
            source = read_urdf_source(target_path)
        except UrdfSourceError as exc:
            print(f"FAIL {display}: {exc}", file=sys.stderr)
            return False
    for caught in caught_warnings:
        print(f"WARN {display}: {caught.message}", file=sys.stderr)
    print(_summary_line(display, source))
    return True


def _summary_line(display: str, source: UrdfSource) -> str:
    return (
        f"OK {display}: robot {source.robot_name!r}, root {source.root_link!r}, "
        f"{len(source.links)} links, {len(source.joints)} joints, "
        f"{len(source.mesh_paths)} resolved mesh references"
    )


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
