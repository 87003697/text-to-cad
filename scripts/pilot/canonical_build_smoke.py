#!/usr/bin/env python3
"""Replay the issue #15 implicit canonical build in the pilot sandbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot.runner import build_bwrap_argv, build_sandbox_environment


EXPECTED_FAILURE = "canonical_build_timeout"
DEFAULT_WORKER_TIMEOUT_SECONDS = 720
SOURCE = '''export default {
  schema: "implicit.js/0.1.0",
  name: "canonical tapered cup",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: `
float outerRadius(float z) {
  float t = clamp((z + 0.50) / 1.00, 0.0, 1.0);
  float body = mix(0.246, 0.338, t);
  float foot = 0.028 * exp(-pow((z + 0.455) / 0.040, 2.0));
  float shoulder = 0.030 * smoothstep(0.27, 0.37, z);
  float rim = 0.022 * exp(-pow((z - 0.405) / 0.050, 2.0));
  return body + foot + shoulder + rim;
}

float sdf(vec3 p) {
  float radial = length(p.xy);
  float outer = max(radial - outerRadius(p.z), max(-0.50 - p.z, p.z - 0.46));
  float innerRadius = outerRadius(p.z) - 0.034;
  float inner = max(innerRadius - radial, max(-0.435 - p.z, p.z - 0.50));
  return max(outer, -inner);
}

vec3 color(vec3 p, vec3 normal) {
  return vec3(0.72, 0.50, 0.26);
}
`,
};
'''

AIRPLANE_ATTEMPT1_SOURCE = '''export default {
  schema: "implicit.js/0.1.0",
  name: "canonical toy airplane",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: `
float ellipsoid(vec3 p, vec3 center, vec3 radii) {
  vec3 q = (p - center) / radii;
  return (length(q) - 1.0) * min(min(radii.x, radii.y), radii.z);
}

float taperedWing(vec3 p, float yCenter, float halfSpan, float rootChord, float tipChord, float thickness) {
  float spanT = clamp(abs(p.y - yCenter) / halfSpan, 0.0, 1.0);
  float chord = mix(rootChord, tipChord, spanT);
  float leading = mix(0.105, 0.025, spanT);
  float xCenter = leading - chord * 0.5;
  vec3 q = vec3(p.x - xCenter, p.y - yCenter, p.z + 0.005);
  return max(abs(q.y) - halfSpan, max(abs(q.x) - chord * 0.5, abs(q.z) - thickness));
}

float sdf(vec3 p) {
  float fuselage = ellipsoid(p, vec3(0.015, 0.0, 0.015), vec3(0.455, 0.072, 0.078));
  float nose = ellipsoid(p, vec3(0.405, 0.0, 0.010), vec3(0.090, 0.066, 0.068));
  float tailBoom = implicit_capsule(p, vec3(-0.420, 0.0, 0.020), vec3(-0.245, 0.0, 0.020), 0.045);
  float body = implicit_union_round(fuselage, nose, 0.025);
  body = implicit_union_round(body, tailBoom, 0.020);

  float mainWing = taperedWing(p, 0.0, 0.390, 0.280, 0.115, 0.018);
  float tailWing = taperedWing(vec3(p.x + 0.300, p.y, p.z - 0.030), 0.0, 0.205, 0.145, 0.065, 0.012);

  vec3 finP = p - vec3(-0.315, 0.0, 0.090);
  float fin = max(abs(finP.y) - 0.014, max(abs(finP.x + finP.z * 0.35) - 0.075, abs(finP.z) - 0.105));

  float cockpit = ellipsoid(p, vec3(0.135, 0.0, 0.078), vec3(0.135, 0.058, 0.052));
  float engineLeft = implicit_capsule(p, vec3(0.060, 0.205, -0.030), vec3(0.205, 0.205, -0.030), 0.045);
  float engineRight = implicit_capsule(p, vec3(0.060, -0.205, -0.030), vec3(0.205, -0.205, -0.030), 0.045);

  float gearLeft = implicit_capsule(p, vec3(-0.015, 0.145, -0.050), vec3(-0.015, 0.145, -0.145), 0.010);
  float gearRight = implicit_capsule(p, vec3(-0.015, -0.145, -0.050), vec3(-0.015, -0.145, -0.145), 0.010);
  float wheelLeft = implicit_torus(p, vec3(-0.015, 0.145, -0.148), vec3(0.0, 1.0, 0.0), 0.026, 0.010);
  float wheelRight = implicit_torus(p, vec3(-0.015, -0.145, -0.148), vec3(0.0, 1.0, 0.0), 0.026, 0.010);
  float noseGear = implicit_capsule(p, vec3(0.305, 0.0, -0.040), vec3(0.305, 0.0, -0.125), 0.008);
  float noseWheel = implicit_torus(p, vec3(0.305, 0.0, -0.130), vec3(0.0, 1.0, 0.0), 0.022, 0.009);

  float model = implicit_union_round(body, mainWing, 0.018);
  model = implicit_union_round(model, tailWing, 0.012);
  model = implicit_union_round(model, fin, 0.012);
  model = implicit_union_round(model, cockpit, 0.020);
  model = implicit_union_round(model, engineLeft, 0.012);
  model = implicit_union_round(model, engineRight, 0.012);
  model = min(model, min(gearLeft, gearRight));
  model = min(model, min(wheelLeft, wheelRight));
  model = min(model, min(noseGear, noseWheel));
  return model;
}

vec3 color(vec3 p, vec3 normal) {
  return vec3(0.72, 0.18, 0.08);
}
`,
};
'''

AIRPLANE_ATTEMPT2_SOURCE = '''export default {
  schema: "implicit.js/0.1.0",
  name: "canonical toy airplane",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  glsl: `
float ellipsoid(vec3 p, vec3 center, vec3 radii) {
  vec3 q = (p - center) / radii;
  return (length(q) - 1.0) * min(min(radii.x, radii.y), radii.z);
}

float wing(vec3 p, float xCenter, float halfSpan, float rootChord, float tipChord, float thickness) {
  float t = clamp(abs(p.y) / halfSpan, 0.0, 1.0);
  float chord = mix(rootChord, tipChord, t);
  float sweep = mix(0.055, -0.020, t);
  vec3 q = vec3(p.x - xCenter - sweep, p.y, p.z);
  return max(abs(q.y) - halfSpan, max(abs(q.x) - chord * 0.5, abs(q.z) - thickness));
}

float sdf(vec3 p) {
  float fuselage = ellipsoid(p, vec3(0.015, 0.0, 0.018), vec3(0.470, 0.070, 0.075));
  float mainWing = wing(p - vec3(0.015, 0.0, -0.005), -0.025, 0.395, 0.285, 0.105, 0.016);
  float tailWing = wing(p - vec3(-0.310, 0.0, 0.030), 0.0, 0.205, 0.145, 0.060, 0.011);
  vec3 finP = p - vec3(-0.325, 0.0, 0.103);
  float fin = max(abs(finP.y) - 0.013, max(abs(finP.x + 0.35 * finP.z) - 0.065, abs(finP.z) - 0.095));
  float cockpit = ellipsoid(p, vec3(0.145, 0.0, 0.078), vec3(0.125, 0.052, 0.047));
  float leftEngine = implicit_capsule(p, vec3(0.035, 0.205, -0.030), vec3(0.190, 0.205, -0.030), 0.041);
  float rightEngine = implicit_capsule(p, vec3(0.035, -0.205, -0.030), vec3(0.190, -0.205, -0.030), 0.041);
  return min(min(min(fuselage, mainWing), min(tailWing, fin)), min(cockpit, min(leftEngine, rightEngine)));
}

vec3 color(vec3 p, vec3 normal) {
  return vec3(0.72, 0.18, 0.08);
}
`,
};
'''

SOURCE_CASES = {
    "tapered-cup-attempt1": SOURCE,
    "airplane-attempt1": AIRPLANE_ATTEMPT1_SOURCE,
    "airplane-attempt2": AIRPLANE_ATTEMPT2_SOURCE,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay the issue #15 canonical implicit build without a provider.",
    )
    parser.add_argument(
        "--worker-timeout-seconds",
        type=int,
        help="Inject a temporary versioned execution profile for calibration.",
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        help="Replay a repo-relative implicit source instead of the tapered cup fixture.",
    )
    parser.add_argument(
        "--source-case",
        choices=sorted(SOURCE_CASES),
        default="tapered-cup-attempt1",
        help="Select a source recovered from issue #15 pilot evidence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.worker_timeout_seconds is not None and not (
        1 <= args.worker_timeout_seconds <= 900
    ):
        raise SystemExit("--worker-timeout-seconds must be between 1 and 900")
    host_env = dict(os.environ)
    host_env["VENUS_TOKEN"] = "provider-free-canonical-build-smoke"
    mounted_input = REPO_ROOT / "models/toys4k/cup_cup_033.ply"
    source_text = SOURCE_CASES[args.source_case]
    if args.source_file is not None:
        requested_source = (REPO_ROOT / args.source_file).resolve()
        try:
            requested_source.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise SystemExit("--source-file must stay within the repository") from exc
        if (
            not requested_source.is_file()
            or requested_source.is_symlink()
            or requested_source.suffix not in {".js", ".mjs"}
            or ".implicit." not in requested_source.name
        ):
            raise SystemExit("--source-file must name a regular .implicit.js/.mjs file")
        source_text = requested_source.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(
        prefix="issue15-canonical-build-smoke-",
        dir=REPO_ROOT / "outputs",
    ) as experiment_text:
        experiment = Path(experiment_text)
        source = experiment / "work/model.implicit.js"
        source.parent.mkdir(parents=True)
        source.write_text(source_text, encoding="utf-8")
        execution_profile = experiment / "work/execution-profile.json"
        if args.worker_timeout_seconds is not None:
            execution_profile.write_text(
                json.dumps(
                    {
                        "schema": "mesh-to-cad.implicit-execution-profile/1",
                        "id": "implicit_canonical_worker_calibration/1",
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
        command = (
            f"cd {sandbox_experiment} && "
            "node /home/pilot/.codex/skills/implicit-cad/scripts/"
            "canonical-build.mjs "
            "--source work/model.implicit.js "
            "--output-dir work/attempt-1-candidate "
            + (
                "--execution-profile work/execution-profile.json "
                if args.worker_timeout_seconds is not None
                else ""
            )
            + "--json"
        )
        argv = build_bwrap_argv(
            REPO_ROOT,
            experiment,
            [mounted_input],
            ["bash", "-lc", command],
            host_env,
        )
        child_env = build_sandbox_environment(
            host_env,
            "http://127.0.0.1:9",
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
                    "schema": "issue15.canonical-build-smoke/1",
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "returncode": result.returncode,
                    "worker_timeout_seconds": (
                        args.worker_timeout_seconds
                        or DEFAULT_WORKER_TIMEOUT_SECONDS
                    ),
                },
                sort_keys=True,
            )
        )
        if result.returncode == 0:
            return 0
        if EXPECTED_FAILURE in result.stdout:
            return 91
        return 92


if __name__ == "__main__":
    raise SystemExit(main())
