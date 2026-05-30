from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PACKAGE_EXPORT_SCRIPT = SCRIPTS_DIR / "packages" / "implicitjs" / "scripts" / "export-implicit-cad.mjs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/export",
        description="Export browser-native implicit CAD modules to GLB, STL, or 3MF meshes.",
    )
    parser.add_argument("--input", "-i", required=True, help="Input .implicit.js/.implicit.mjs file.")
    parser.add_argument("--output", "-o", help="Output path. Defaults next to the input with the selected format.")
    parser.add_argument("--format", "-f", choices=("glb", "stl", "3mf"), help="Export format.")
    parser.add_argument("--resolution", type=int, help="Longest-axis sampling resolution. Default: 96.")
    parser.add_argument("--max-cells", type=int, help="Safety cap for sampled grid cells.")
    parser.add_argument("--params", help="Implicit parameter JSON object.")
    parser.add_argument("--animation", help="Implicit animation-state JSON object.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not PACKAGE_EXPORT_SCRIPT.is_file():
        parser.error(
            f"missing implicitjs package runtime: {PACKAGE_EXPORT_SCRIPT}. "
            "Restore the development symlink layout or rebuild the production skill bundle."
        )

    command = ["node", str(PACKAGE_EXPORT_SCRIPT), "--input", args.input]
    if args.output:
        command.extend(["--output", args.output])
    if args.format:
        command.extend(["--format", args.format])
    if args.resolution:
        command.extend(["--resolution", str(args.resolution)])
    if args.max_cells:
        command.extend(["--max-cells", str(args.max_cells)])
    if args.params:
        command.extend(["--params", args.params])
    if args.animation:
        command.extend(["--animation", args.animation])
    if args.json:
        command.append("--json")

    completed = subprocess.run(command)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
