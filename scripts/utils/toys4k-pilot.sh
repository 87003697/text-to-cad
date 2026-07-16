#!/usr/bin/env bash
# Pilot runner for Toys4K mesh-to-CAD benchmark.
#
# Usage:
#   scripts/utils/toys4k-pilot.sh <object_name>
# Example:
#   scripts/utils/toys4k-pilot.sh cup_cup_033
#
# Assumes the corresponding PLY exists at models/toys4k/<object_name>.ply.
# Outputs go to outputs/<timestamp>-<object_name>/ (git-ignored).
# Requires:
#   - gateway/codex-gpt56 launcher
#   - codex CLI at /opt/homebrew/bin/codex
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OBJ="${1:?Usage: run_pilot.sh <object_name>}"
PLY="models/toys4k/${OBJ}.ply"
[[ -f "$PLY" ]] || { echo "Missing mesh: $PLY" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
EXP_DIR="outputs/${TS}-${OBJ}"
mkdir -p "$EXP_DIR"

PROMPT=$(cat <<EOF
You are converting a 3D mesh into a parametric CAD model.

Input mesh: ${PLY}
Working output directory (write all artifacts here): ${EXP_DIR}

Workflow:
1. Inspect the mesh:
   - Run skills/mesh-to-cad/scripts/mesh-inspect to get stats (volume, surface area, watertight, Euler characteristic, PCA principal axes).
   - Read skills/mesh-to-cad/references/mesh-analysis.md for interpretation guidance.
2. Route decision — pick exactly one:
   - cad skill (skills/cad/SKILL.md): geometric/machinable objects; produces STEP via build123d.
   - implicit-cad skill (skills/implicit-cad/SKILL.md): organic/free-form; produces .implicit.js via GLSL SDF.
   Justify the choice from the mesh statistics in one paragraph.
3. Follow the SKILL.md of the chosen skill to reconstruct the model. Save the primary artifact (STEP or .implicit.js) plus any intermediate build scripts under ${EXP_DIR}.
4. Write ${EXP_DIR}/notes.md with:
   - chosen route + one-paragraph justification
   - key mesh stats used
   - list of CAD operations you invoked (extrude / revolve / loft / sweep / booleans / fillet / shell / pattern / assembly, or implicit-cad equivalents)
   - self-assessed quality on 0-10 scale
   - known limitations or approximations

Stay under ${EXP_DIR}; do not modify skills/ or packages/.
EOF
)

echo "[pilot] object=${OBJ}"
echo "[pilot] output=${EXP_DIR}"
echo "[pilot] launching gpt-5.6-sol..."

# --json emits JSONL events including usage; capture to events.jsonl for token accounting.
# -s workspace-write scopes writes to the repo (needed so codex can write outputs/).
gateway/codex-gpt56 sol exec \
    --json \
    -s workspace-write \
    "$PROMPT" \
    > "${EXP_DIR}/events.jsonl" \
    2> "${EXP_DIR}/stderr.log"

echo "[pilot] done. Extracting token usage..."

# Parse token usage from JSONL events (last usage event wins).
python3 - <<PY
import json, pathlib, sys
p = pathlib.Path("${EXP_DIR}/events.jsonl")
usage = None
for line in p.read_text().splitlines():
    if not line.strip():
        continue
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        continue
    u = evt.get("usage") or evt.get("token_usage") or (evt.get("msg") or {}).get("token_usage")
    if u:
        usage = u
if usage is None:
    print("(no usage event found; check events.jsonl manually)")
    sys.exit(0)
inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
out = usage.get("output_tokens", usage.get("completion_tokens", 0))
cached = usage.get("cached_input_tokens", usage.get("cache_read_input_tokens", 0))
# Venus GPT-5.6-sol pricing
uncached_in = max(inp - cached, 0)
cost = uncached_in * 5e-6 + cached * 0.5e-6 + out * 30e-6
summary = {
    "object": "${OBJ}",
    "input_tokens": inp,
    "cached_input_tokens": cached,
    "output_tokens": out,
    "estimated_cost_usd": round(cost, 4),
}
print(json.dumps(summary, indent=2))
pathlib.Path("${EXP_DIR}/usage.json").write_text(json.dumps(summary, indent=2))
PY

echo "[pilot] artifacts:"
ls -la "${EXP_DIR}"
