#!/usr/bin/env bash
# scripts/pilot/toys4k-pilot.sh <object_name> <group> [exp_name]
# Toys4K mesh-to-CAD benchmark pilot. Reads models/toys4k/<name>.ply,
# writes outputs/<group>/<TS>-<name>/. Group format: YYYYMMDD-HHMMSS-<slug>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"

OBJ="${1:?Usage: toys4k-pilot.sh <object_name> <group>}"
GROUP="${2:?Usage: toys4k-pilot.sh <object_name> <group>}"
[[ "$GROUP" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]] \
    || { echo "Bad group: '$GROUP'. Expect YYYYMMDD-HHMMSS-<slug>." >&2; exit 1; }

PLY="models/toys4k/${OBJ}.ply"
case "$OBJ" in
    bottle_bottle_089)
        EXPECTED_BYTES=110741
        EXPECTED_SHA256=80353ef44563ac1eaeec84d1188059ad5ab373aa1e258d710588e6650789e214
        ROUTE=cad
        ROUTE_EVIDENCE="routing-rubric agent judgment: machinable hard-surface form"
        ;;
    toaster_toaster_005)
        EXPECTED_BYTES=53914
        EXPECTED_SHA256=ee28c82344d82425d4c10840aff55365679f6d7154f09bdb753d112e776cd605
        ROUTE=cad
        ROUTE_EVIDENCE="routing-rubric agent judgment: machinable hard-surface form"
        ;;
    mushroom_mushroom_018)
        EXPECTED_BYTES=68280
        EXPECTED_SHA256=49d27f6e853a80fc9450e5e650deaa34b0d731c37cba39838a88901385362990
        ROUTE=implicit-cad
        ROUTE_EVIDENCE="routing-rubric organic or plant form"
        ;;
    airplane_airplane_016)
        EXPECTED_BYTES=10475507
        EXPECTED_SHA256=72abf42e0efc7cb7023d10b7677a20c16a28b03adf05ed6414eae6e102f562d9
        ROUTE=implicit-cad
        ROUTE_EVIDENCE="routing-rubric face-count-over-100k; current PLY header element face 305796"
        ;;
    *)
        echo "Unknown Toys4K fixture key: '$OBJ'" >&2
        exit 1
        ;;
esac
[[ -f "$PLY" && ! -L "$PLY" ]] || { echo "Missing or non-regular mesh: $PLY" >&2; exit 1; }
read -r OBSERVED_BYTES OBSERVED_SHA256 < <(
    "$PYTHON_BIN" -c \
        'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); b=p.read_bytes(); print(len(b),hashlib.sha256(b).hexdigest())' \
        "$PLY"
)
[[ "$OBSERVED_BYTES" == "$EXPECTED_BYTES" && "$OBSERVED_SHA256" == "$EXPECTED_SHA256" ]] \
    || { echo "Immutable fixture identity mismatch: $PLY" >&2; exit 1; }

EXP_NAME="${3:-$(date +%Y%m%d-%H%M%S)-${OBJ}}"
if [[ ! "$EXP_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ \
    || "$EXP_NAME" == "." || "$EXP_NAME" == ".." ]]; then
    echo "Unsafe exp name: '$EXP_NAME'" >&2
    exit 1
fi
EXP_DIR="outputs/${GROUP}/${EXP_NAME}"
[[ ! -e "$EXP_DIR" && ! -L "$EXP_DIR" ]] \
    || { echo "Experiment output must be fresh: $EXP_DIR" >&2; exit 1; }

# Credentials are admitted only after all local input/path preparation closes.
SECRETS_FILE="${HOME}/.secrets/text-to-cad.env"
if [[ -z "${VENUS_TOKEN:-}" && -f "$SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
fi

# Minimal orchestrator prompt — peer skills, references, and commit
# conventions live in skills/mesh-to-cad/SKILL.md; duplicating them here
# would drift.
PROMPT=$(cat <<EOF
You are the \$mesh-to-cad skill orchestrator. Follow
skills/mesh-to-cad/SKILL.md verbatim; it is the authoritative contract.
This run is Development/MVP — Not Sealed, Not Formal, Not Verified, Not Production.

Input mesh: ${PLY}
Experiment directory (already an initialized local git repo, write ALL
artifacts here): ${EXP_DIR}

The canonical Workspace and its atomically published Final Delivery define
success. Optional additional human review material belongs under
${EXP_DIR}/reviews/ and never substitutes for formal Measured Step or Final
Delivery previews.

Closed route: ${ROUTE}. Do not silently substitute another route.
Route evidence: ${ROUTE_EVIDENCE} from
skills/mesh-to-cad/references/routing-rubric.md.
Depth 1 through 8 objective measurement evidence is required for every
Measured Step and the Final Delivery.

Do not call `view_image` in this Venus-backed pilot: its Responses
continuation rejects image tool output. Still generate and cite every required
PNG, and use the formal preview JSON plus objective measurements for decisions.

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
    --disable
    plugins
    -s
    danger-full-access
    "$PROMPT"
)

PILOT_EXIT=0
JOB_SUFFIX=$("$PYTHON_BIN" -c \
    'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])' \
    "$EXP_NAME")
JOB_ID="toys4k-${OBJ//_/-}-${JOB_SUFFIX}"
"$PYTHON_BIN" "$SCRIPT_DIR/runner.py" run --input "$PLY" \
    --development-job-id "$JOB_ID" \
    --development-ledger "${EXP_DIR}/run/development-ledger.jsonl" \
    --development-total-ledger "outputs/${GROUP}/development-total-ledger.jsonl" \
    "$EXP_DIR" -- \
    "${WORKLOAD[@]}" < /dev/null > /dev/null \
    2> "${EXP_DIR}/run/stderr.log" || PILOT_EXIT=$?

if [[ $PILOT_EXIT -ne 0 ]]; then
    exit "$PILOT_EXIT"
fi

echo "[pilot] Done. Audit: /pilot-review $EXP_DIR/"
