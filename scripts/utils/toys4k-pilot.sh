#!/usr/bin/env bash
# scripts/utils/toys4k-pilot.sh <object_name> <group>
# Toys4K mesh-to-CAD benchmark pilot. Reads models/toys4k/<name>.ply,
# writes outputs/<group>/<TS>-<name>/. Group format: YYYYMMDD-HHMMSS-<slug>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OBJ="${1:?Usage: toys4k-pilot.sh <object_name> <group>}"
GROUP="${2:?Usage: toys4k-pilot.sh <object_name> <group>}"
[[ "$GROUP" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]] \
    || { echo "Bad group: '$GROUP'. Expect YYYYMMDD-HHMMSS-<slug>." >&2; exit 1; }

PLY="models/toys4k/${OBJ}.ply"
[[ -f "$PLY" ]] || { echo "Missing mesh: $PLY" >&2; exit 1; }

EXP_DIR="outputs/${GROUP}/$(date +%Y%m%d-%H%M%S)-${OBJ}"

# Minimal orchestrator prompt — peer skills, references, and commit
# conventions live in skills/mesh-to-cad/SKILL.md; duplicating them here
# would drift.
PROMPT=$(cat <<EOF
You are the \$mesh-to-cad skill orchestrator. Follow
skills/mesh-to-cad/SKILL.md verbatim; it is the authoritative contract.

Input mesh: ${PLY}
Experiment directory (already an initialized local git repo, write ALL
artifacts here): ${EXP_DIR}

Stay under ${EXP_DIR}; do not modify skills/, packages/, or files outside.
EOF
)

echo "[pilot] $OBJ → $EXP_DIR"

eval "$("${SCRIPT_DIR}/codex-init.sh" "${EXP_DIR}")"
printf '%s' "$PROMPT" > "${EXP_DIR}/prompt.txt"

CODEX_EXIT=0
$CODEX_RUN "$PROMPT" \
    < /dev/null > /dev/null 2> "${EXP_DIR}/stderr.log" \
    || CODEX_EXIT=$?

"${SCRIPT_DIR}/codex-exit.sh" "${EXP_DIR}" "$CODEX_EXIT"

echo "[pilot] Done. Audit: /pilot-review $EXP_DIR/"
