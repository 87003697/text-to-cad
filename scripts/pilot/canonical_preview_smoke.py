#!/usr/bin/env python3
"""Replay one canonical residual preview in the production pilot sandbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot.runner import build_bwrap_argv, build_sandbox_environment
from scripts.pilot.canonical_build_smoke import SOURCE_CASES

sys.path.insert(0, str(REPO_ROOT / "packages/meshshot/src"))

from meshshot import load_profile


def repo_path(value: str, label: str) -> Path:
    path = (REPO_ROOT / value).resolve()
    try:
        path.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} must stay within the repository") from exc
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a provider-free formal preview from preserved evidence.",
    )
    parser.add_argument("--fixture-experiment", required=True)
    candidate = parser.add_mutually_exclusive_group(required=True)
    candidate.add_argument("--candidate")
    candidate.add_argument("--source-file")
    candidate.add_argument("--source-case", choices=sorted(SOURCE_CASES))
    parser.add_argument(
        "--worker-timeout-seconds",
        type=int,
        help="Optional temporary canonical-build deadline for calibration.",
    )
    parser.add_argument(
        "--preserve-candidate",
        help="Copy a source-built GLB to a new repo-relative outputs/ path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = repo_path(args.fixture_experiment, "--fixture-experiment")
    candidate = repo_path(args.candidate, "--candidate") if args.candidate else None
    source = repo_path(args.source_file, "--source-file") if args.source_file else None
    source_text = SOURCE_CASES.get(args.source_case) if args.source_case else None
    preserved_candidate = (
        repo_path(args.preserve_candidate, "--preserve-candidate")
        if args.preserve_candidate
        else None
    )
    reference = fixture / "input"
    experiment_json = fixture / "experiment.json"
    if not reference.is_dir() or not experiment_json.is_file():
        raise SystemExit("fixture experiment lacks input/ or experiment.json")
    if candidate is not None and (
        not candidate.is_file() or candidate.suffix.lower() != ".glb"
    ):
        raise SystemExit("--candidate must name a preserved GLB file")
    if source is not None and (
        not source.is_file()
        or source.is_symlink()
        or source.suffix not in {".js", ".mjs"}
        or ".implicit." not in source.name
    ):
        raise SystemExit("--source-file must name a regular .implicit.js/.mjs file")
    if preserved_candidate is not None:
        if source is None and source_text is None:
            raise SystemExit("--preserve-candidate requires --source-file/--source-case")
        try:
            preserved_candidate.relative_to(REPO_ROOT / "outputs")
        except ValueError as exc:
            raise SystemExit("--preserve-candidate must stay within outputs/") from exc
        if preserved_candidate.suffix.lower() != ".glb":
            raise SystemExit("--preserve-candidate must name a .glb file")
        if preserved_candidate.exists():
            raise SystemExit("--preserve-candidate target already exists")
    if args.worker_timeout_seconds is not None and not (
        1 <= args.worker_timeout_seconds <= 900
    ):
        raise SystemExit("--worker-timeout-seconds must be between 1 and 900")

    host_env = dict(os.environ)
    host_env["VENUS_TOKEN"] = "provider-free-canonical-preview-smoke"
    mounted_input = REPO_ROOT / "models/toys4k/airplane_airplane_016.ply"

    with tempfile.TemporaryDirectory(
        prefix="issue15-canonical-preview-smoke-",
        dir=REPO_ROOT / "outputs",
    ) as experiment_text:
        experiment = Path(experiment_text)
        shutil.copytree(reference, experiment / "input")
        experiment_payload = json.loads(experiment_json.read_text(encoding="utf-8"))
        loaded_profile = load_profile()
        experiment_payload["preview_profile"] = {
            "name": loaded_profile.profile["name"],
            "sha256": loaded_profile.sha256,
        }
        (experiment / "experiment.json").write_text(
            json.dumps(experiment_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        work = experiment / "work"
        work.mkdir()
        if candidate is not None:
            shutil.copy2(candidate, work / "candidate.glb")
        else:
            candidate_source = work / "source/model.implicit.js"
            candidate_source.parent.mkdir(parents=True)
            if source is not None:
                shutil.copy2(source, candidate_source)
            else:
                candidate_source.write_text(source_text, encoding="utf-8")
            if args.worker_timeout_seconds is not None:
                (work / "execution-profile.json").write_text(
                    json.dumps(
                        {
                            "schema": "mesh-to-cad.implicit-execution-profile/1",
                            "id": "implicit_canonical_worker_preview_smoke/1",
                            "worker_timeout_ms": args.worker_timeout_seconds * 1000,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        sandbox_experiment = Path("/workspace/repo") / experiment.relative_to(
            REPO_ROOT
        )
        child_env = build_sandbox_environment(host_env, "http://127.0.0.1:9")
        build_elapsed_seconds = None
        if source is not None or source_text is not None:
            build_command = (
                f"cd {sandbox_experiment} && "
                "node /home/pilot/.codex/skills/implicit-cad/scripts/"
                "canonical-build.mjs --source work/source/model.implicit.js "
                "--output-dir work/candidate "
                + (
                    "--execution-profile work/execution-profile.json "
                    if args.worker_timeout_seconds is not None
                    else ""
                )
                + "--json"
            )
            build_argv = build_bwrap_argv(
                REPO_ROOT,
                experiment,
                [mounted_input],
                ["bash", "-lc", build_command],
                host_env,
            )
            build_started = time.monotonic()
            build_result = subprocess.run(
                build_argv,
                check=False,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            build_elapsed_seconds = time.monotonic() - build_started
            print(build_result.stdout, end="")
            if build_result.returncode != 0:
                print(
                    json.dumps(
                        {
                            "schema": "issue15.canonical-preview-smoke/1",
                            "build_elapsed_seconds": round(build_elapsed_seconds, 3),
                            "build_returncode": build_result.returncode,
                            "preview_returncode": None,
                        },
                        sort_keys=True,
                    )
                )
                return build_result.returncode
            if preserved_candidate is not None:
                preserved_candidate.parent.mkdir(parents=True, exist_ok=True)
                with preserved_candidate.open("xb") as destination:
                    destination.write(
                        (work / "candidate/artifacts/model.glb").read_bytes()
                    )
        command = (
            "python /home/pilot/.codex/skills/mesh-compare/scripts/mesh-compare "
            + (
                "voxblame-preview work/candidate/artifacts/model.glb "
                if source is not None or source_text is not None
                else "voxblame-preview work/candidate.glb "
            )
            + "--reference input --experiment experiment.json "
            "--output work/preview --variant step"
        )
        argv = build_bwrap_argv(
            REPO_ROOT,
            experiment,
            [mounted_input],
            ["bash", "-lc", f"cd {sandbox_experiment} && {command}"],
            host_env,
        )
        started = time.monotonic()
        result = subprocess.run(
            argv,
            check=False,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        elapsed_seconds = time.monotonic() - started
        print(result.stdout, end="")
        print(
            json.dumps(
                {
                    "schema": "issue15.canonical-preview-smoke/1",
                    "build_elapsed_seconds": (
                        round(build_elapsed_seconds, 3)
                        if build_elapsed_seconds is not None
                        else None
                    ),
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "preview_returncode": result.returncode,
                },
                sort_keys=True,
            )
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
