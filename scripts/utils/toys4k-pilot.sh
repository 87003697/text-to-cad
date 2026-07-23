#!/usr/bin/env bash
# Pilot runner for Toys4K mesh-to-CAD benchmark.
#
# Usage:
#   scripts/utils/toys4k-pilot.sh <object_name>
# Example:
#   scripts/utils/toys4k-pilot.sh cup_cup_033
#
# Assumes the corresponding PLY exists at models/toys4k/<object_name>.ply.
# Outputs go to outputs/<timestamp>-<object_name>/ (git-ignored). Each
# experiment directory is initialized as an independent local git repo
# so agents can commit per-phase and per-iteration; see
# skills/mesh-to-cad/references/output-schemas.md § Git commit conventions.
#
# Requires:
#   - gateway/codex-gpt56 launcher
#   - codex CLI at /opt/homebrew/bin/codex (or on $PATH)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OBJ="${1:?Usage: toys4k-pilot.sh <object_name>}"
PLY="models/toys4k/${OBJ}.ply"
[[ -f "$PLY" ]] || { echo "Missing mesh: $PLY" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
EXP_DIR="outputs/${TS}-${OBJ}"
mkdir -p "$EXP_DIR"

# Initialize the experiment directory as an independent local git repo so
# the agent can commit per phase (Setup / iter N / Finalization) per the
# schema in skills/mesh-to-cad/references/output-schemas.md.
(
    cd "$EXP_DIR"
    git init --quiet
    cat > .gitignore <<'GITIGNORE'
stderr.log
events.jsonl
usage.json
__pycache__/
*.pyc
.codex/
GITIGNORE
    git add .gitignore
    git -c user.name="pilot" -c user.email="pilot@localhost" \
        commit --quiet -m "pilot: initial commit (empty experiment scaffold)"
)

PROMPT=$(cat <<EOF
You are the \$mesh-to-cad skill orchestrator. Follow the SKILL.md at
skills/mesh-to-cad/SKILL.md verbatim; it is the authoritative contract.

Input mesh: ${PLY}
Experiment directory (\${EXP_DIR}, already an initialized local git repo,
write ALL artifacts here): ${EXP_DIR}

Peer skills to delegate to (invoke by name in workflow steps 1, 3, 4, 5, 6):
- \$mesh-inspect  → skills/mesh-inspect/SKILL.md
- \$cad           → skills/cad/SKILL.md
- \$implicit-cad  → skills/implicit-cad/SKILL.md
- \$mesh-compare  → skills/mesh-compare/SKILL.md
- \$cad-viewer    → skills/cad-viewer/SKILL.md  (optional handoff)

Progressive references you must consult (lazy-load per SKILL.md triggers):
- skills/mesh-to-cad/references/routing-rubric.md  (step 2)
- skills/mesh-to-cad/references/output-schemas.md  (steps 2, 3, 4, 5, 7,
  including § Git commit conventions)
- skills/mesh-compare/references/compare-metrics.md  (step 4)
- skills/mesh-compare/references/render-modes.md      (steps 5, 6)

Commit timing inside \${EXP_DIR} (per output-schemas.md § Git commit
conventions):
- Setup phase: one commit at the end of workflow step 1 and step 2.
- Reconstruction loop: one commit at the end of step 5 for each iter,
  with verdict accept/refine/plateau in the message. Divergence stops
  discard the iter via 'git checkout .' (no commit).
- Finalization phase: one commit at the end of step 6 and step 7.

Stay under \${EXP_DIR}; do not modify skills/, packages/, or files
outside the experiment directory.
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
