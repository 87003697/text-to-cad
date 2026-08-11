#!/usr/bin/env python3
"""Run the public VoxBlame CLI inside the production pilot sandbox."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot.runner import build_bwrap_argv, build_sandbox_environment


EXPECTED_FAILURE = "native octree backend is unavailable"


def main() -> int:
    repo_root = REPO_ROOT
    source = repo_root / "models/toys4k/cup_cup_033.ply"
    host_env = dict(os.environ)
    # build_bwrap_argv requires the production pilot environment contract, but
    # this smoke never starts Codex, claude-tap, or a provider request.
    host_env["VENUS_TOKEN"] = "provider-free-native-octree-smoke"

    with tempfile.TemporaryDirectory(
        prefix="issue15-native-octree-smoke-",
        dir=repo_root / "outputs",
    ) as experiment_text:
        experiment = Path(experiment_text)
        sandbox_experiment = Path("/workspace/repo") / experiment.relative_to(
            repo_root
        )
        skill_cli = (
            "/home/pilot/.codex/skills/mesh-compare/scripts/mesh-compare"
        )
        command = " && ".join(
            (
                f"python {skill_cli} voxblame-prepare-reference "
                "/workspace/repo/models/toys4k/cup_cup_033.ply "
                f"--output {sandbox_experiment}/input",
                f"python {skill_cli} voxblame-measure "
                f"{sandbox_experiment}/input/reference.ply "
                f"--reference {sandbox_experiment}/input "
                f"--output {sandbox_experiment}/measurement --step 0",
            )
        )
        argv = build_bwrap_argv(
            repo_root,
            experiment,
            [source],
            ["bash", "-lc", command],
            host_env,
        )
        child_env = build_sandbox_environment(
            host_env,
            "http://127.0.0.1:9",
        )
        result = subprocess.run(
            argv,
            check=False,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(result.stdout, end="")
        if result.returncode == 0:
            return 0
        if EXPECTED_FAILURE in result.stdout:
            return 91
        return 92


if __name__ == "__main__":
    raise SystemExit(main())
