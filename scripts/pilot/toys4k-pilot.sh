#!/usr/bin/env bash
# scripts/pilot/toys4k-pilot.sh <object_name> <group> [exp_name]
# Toys4K mesh-to-CAD benchmark pilot. Reads models/toys4k/<name>.ply,
# writes outputs/<group>/<TS>-<name>/. Group format: YYYYMMDD-HHMMSS-<slug>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Defensive: source secrets so `nohup ./toys4k-pilot.sh ...` works without
# a caller wrapper. The direct Venus provider still needs VENUS_TOKEN; nohup
# does not source shell startup files. A missing file is fine for local tests.
SECRETS_FILE="${HOME}/.secrets/text-to-cad.env"
if [[ -z "${VENUS_TOKEN:-}" && -f "$SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
fi

OBJ="${1:?Usage: toys4k-pilot.sh <object_name> <group>}"
GROUP="${2:?Usage: toys4k-pilot.sh <object_name> <group>}"
[[ "$GROUP" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]] \
    || { echo "Bad group: '$GROUP'. Expect YYYYMMDD-HHMMSS-<slug>." >&2; exit 1; }

PLY="models/toys4k/${OBJ}.ply"
[[ -f "$PLY" ]] || { echo "Missing mesh: $PLY" >&2; exit 1; }

EXP_NAME="${3:-$(date +%Y%m%d-%H%M%S)-${OBJ}}"
if [[ ! "$EXP_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ \
    || "$EXP_NAME" == "." || "$EXP_NAME" == ".." ]]; then
    echo "Unsafe exp name: '$EXP_NAME'" >&2
    exit 1
fi
EXP_DIR="outputs/${GROUP}/${EXP_NAME}"

# Minimal orchestrator prompt — peer skills, references, and commit
# conventions live in skills/mesh-to-cad/SKILL.md; duplicating them here
# would drift.
PROMPT=$(cat <<EOF
You are the \$mesh-to-cad skill orchestrator. Follow
skills/mesh-to-cad/SKILL.md verbatim; it is the authoritative contract.

Input mesh: ${PLY}
Experiment directory (already an initialized local git repo, write ALL
artifacts here): ${EXP_DIR}

The canonical Workspace and its atomically published Final Delivery define
success. Optional additional human review material belongs under
${EXP_DIR}/reviews/ and never substitutes for formal Measured Step or Final
Delivery previews.

Stay under ${EXP_DIR}; do not modify skills/, packages/, or files outside.
EOF
)

echo "[pilot] $OBJ → $EXP_DIR"

mkdir -p "$EXP_DIR/run"
printf '%s' "$PROMPT" > "${EXP_DIR}/run/prompt.txt"

WORKLOAD=(
    "gateway/codex-tap-gpt56"
    "${MODEL:-sol}"
    exec
    --skip-git-repo-check
    -s
    danger-full-access
    "$PROMPT"
)

PILOT_EXIT=0
"$PYTHON_BIN" "$SCRIPT_DIR/runner.py" run --input "$PLY" "$EXP_DIR" -- \
    "${WORKLOAD[@]}" < /dev/null > /dev/null \
    2> "${EXP_DIR}/run/stderr.log" || PILOT_EXIT=$?

if [[ $PILOT_EXIT -ne 0 ]]; then
    exit "$PILOT_EXIT"
fi

echo "[pilot] Done. Audit: /pilot-review $EXP_DIR/"
