from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(SCRIPTS_DIR))

from sdf.external import run_gz_sdf_check
from sdf.source import SdfSource, SdfSourceError, parse_sdf_xml
from sdf.validation import validate_sdf_xml

SDF_SUFFIX = ".sdf"


def validate_sdf_targets(
    targets: Sequence[str],
    *,
    gz_check: str = "auto",
    strict: bool = False,
) -> int:
    target_paths = [_resolve_target_path(target) for target in targets]
    failed = False
    for target_path in target_paths:
        if not _validate_target(target_path, gz_check=gz_check, strict=strict):
            failed = True
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Validate explicit SDFormat/SDF targets.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Explicit .sdf file to validate.",
    )
    parser.add_argument(
        "--gz-check",
        choices=("auto", "required", "never"),
        default="auto",
        help="Optionally run 'gz sdf --check' as external validation.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat bundled validation warnings as failures.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return validate_sdf_targets(args.targets, gz_check=args.gz_check, strict=args.strict)


def _resolve_target_path(raw_target: object) -> Path:
    value = str(raw_target or "").strip()
    if not value:
        raise ValueError("sdf target must be a non-empty path")
    target_path = Path(value).expanduser()
    return target_path.resolve() if target_path.is_absolute() else (Path.cwd() / target_path).resolve()


def _validate_target(target_path: Path, *, gz_check: str, strict: bool) -> bool:
    display = _display_path(target_path)
    if target_path.suffix.lower() != SDF_SUFFIX:
        print(f"FAIL {display}: target must be a .sdf file", file=sys.stderr)
        return False
    if not target_path.is_file():
        print(f"FAIL {display}: file not found", file=sys.stderr)
        return False
    try:
        xml_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL {display}: {exc}", file=sys.stderr)
        return False

    validation = validate_sdf_xml(xml_text, source_path=target_path, base_dir=target_path.parent)
    validation.extend(run_gz_sdf_check(xml_text, output_path=target_path, mode=gz_check))

    for finding in [*validation.warnings, *validation.infos]:
        print(finding.format(), file=sys.stderr)
    failing = validation.errors + (validation.warnings if strict else [])
    if failing:
        for finding in validation.errors:
            print(finding.format(), file=sys.stderr)
        print(f"FAIL {display}: {len(failing)} blocking finding(s)", file=sys.stderr)
        return False

    try:
        source = parse_sdf_xml(xml_text, source_path=target_path, base_dir=target_path.parent)
    except SdfSourceError as exc:
        print(f"FAIL {display}: {exc}", file=sys.stderr)
        return False
    print(_summary_line(display, source))
    return True


def _summary_line(display: str, source: SdfSource) -> str:
    scope = []
    if source.world_names:
        scope.append(f"worlds {list(source.world_names)!r}")
    if source.model_names:
        scope.append(f"models {list(source.model_names)!r}")
    scope_text = ", ".join(scope) if scope else "no models or worlds"
    return (
        f"OK {display}: SDF {source.version}, {scope_text}, "
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
