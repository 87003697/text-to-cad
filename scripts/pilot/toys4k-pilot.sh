#!/usr/bin/env bash
# scripts/pilot/toys4k-pilot.sh <object_name> <group> [exp_name] [direct|e2e] [--view-image|--no-view-image] [--reconstruction-spec|--no-reconstruction-spec]
# MODEL selects the Venus gateway variant; it defaults to the public gpt-5.5 slug.
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

USAGE="Usage: toys4k-pilot.sh <object_name> <group> [exp_name] [direct|e2e] [--view-image|--no-view-image] [--reconstruction-spec|--no-reconstruction-spec] (defaults: view_image on, Reconstruction Spec on)"
OBJ="${1:?$USAGE}"
GROUP="${2:?$USAGE}"
[[ "$GROUP" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]] \
    || { echo "Bad group: '$GROUP'. Expect YYYYMMDD-HHMMSS-<slug>." >&2; exit 1; }

PLY="models/toys4k/${OBJ}.ply"
[[ -f "$PLY" ]] || { echo "Missing mesh: $PLY" >&2; exit 1; }

EXP_NAME="$(date +%Y%m%d-%H%M%S)-${OBJ}"
PLUGIN_MODE="direct"
VIEW_IMAGE=1
VIEW_IMAGE_FLAG_SEEN=0
RECONSTRUCTION_SPEC=1
RECONSTRUCTION_SPEC_FLAG_SEEN=0
PILOT_ARGS=("${@:3}")
ARG_INDEX=0

# Keep the historical positional form while allowing the reconstruction flag
# wherever the old opt-in flag was accepted (with or without an explicit exp
# name and plugin mode).
if (( ARG_INDEX < ${#PILOT_ARGS[@]} )) \
    && [[ "${PILOT_ARGS[$ARG_INDEX]}" != --* ]]; then
    EXP_NAME="${PILOT_ARGS[$ARG_INDEX]}"
    ((ARG_INDEX += 1))
fi
if (( ARG_INDEX < ${#PILOT_ARGS[@]} )) \
    && [[ "${PILOT_ARGS[$ARG_INDEX]}" != --* ]]; then
    PLUGIN_MODE="${PILOT_ARGS[$ARG_INDEX]}"
    ((ARG_INDEX += 1))
fi
while (( ARG_INDEX < ${#PILOT_ARGS[@]} )); do
    case "${PILOT_ARGS[$ARG_INDEX]}" in
        --view-image)
            [[ "$VIEW_IMAGE_FLAG_SEEN" == 0 ]] \
                || { echo "Duplicate or conflicting view_image flag." >&2; exit 2; }
            VIEW_IMAGE=1
            VIEW_IMAGE_FLAG_SEEN=1
            ;;
        --no-view-image)
            [[ "$VIEW_IMAGE_FLAG_SEEN" == 0 ]] \
                || { echo "Duplicate or conflicting view_image flag." >&2; exit 2; }
            VIEW_IMAGE=0
            VIEW_IMAGE_FLAG_SEEN=1
            ;;
        --reconstruction-spec)
            [[ "$RECONSTRUCTION_SPEC_FLAG_SEEN" == 0 ]] \
                || { echo "Duplicate or conflicting reconstruction spec flag." >&2; exit 2; }
            RECONSTRUCTION_SPEC=1
            RECONSTRUCTION_SPEC_FLAG_SEEN=1
            ;;
        --no-reconstruction-spec)
            [[ "$RECONSTRUCTION_SPEC_FLAG_SEEN" == 0 ]] \
                || { echo "Duplicate or conflicting reconstruction spec flag." >&2; exit 2; }
            RECONSTRUCTION_SPEC=0
            RECONSTRUCTION_SPEC_FLAG_SEEN=1
            ;;
        *)
            echo "Bad pilot option: '${PILOT_ARGS[$ARG_INDEX]}'. Expect --view-image, --no-view-image, --reconstruction-spec, or --no-reconstruction-spec." >&2
            exit 2
            ;;
    esac
    ((ARG_INDEX += 1))
done

if [[ ! "$EXP_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ \
    || "$EXP_NAME" == "." || "$EXP_NAME" == ".." ]]; then
    echo "Unsafe exp name: '$EXP_NAME'" >&2
    exit 1
fi
EXP_DIR="outputs/${GROUP}/${EXP_NAME}"
MODEL_SELECTOR="${MODEL:-gpt-5.5}"
case "$MODEL_SELECTOR" in
    sol|terra|luna|gpt-5.5) ;;
    *)
        echo "Bad model selector: '$MODEL_SELECTOR'. Expect sol|terra|luna|gpt-5.5." >&2
        exit 2
        ;;
esac
case "$PLUGIN_MODE" in
    direct|e2e) ;;
    *)
        echo "Bad plugin mode: '$PLUGIN_MODE'. Expect direct or e2e." >&2
        exit 2
        ;;
esac

RECONSTRUCTION_SPEC_INSTRUCTION=""
if [[ "$RECONSTRUCTION_SPEC" == 1 ]]; then
    RECONSTRUCTION_SPEC_INSTRUCTION=$(cat <<EOF
Reconstruction Spec is enabled for this pilot. After raw-mesh inspection and
Canonical Reference preparation, create and maintain the mutable file
${EXP_DIR}/run/reconstruction-spec.json. Read it before initial CAD authoring
and before each Repair Hypothesis; update it in place when geometric
understanding changes. Keep it under run/ outside Workspace authority.
EOF
    )
else
    RECONSTRUCTION_SPEC_INSTRUCTION=$(cat <<EOF
Reconstruction Spec is disabled for this run. Do not create, read, or update
${EXP_DIR}/run/reconstruction-spec.json; continue without this working
document.
EOF
    )
fi

VIEW_IMAGE_INSTRUCTION=""
if [[ "$VIEW_IMAGE" == 1 ]]; then
    VIEW_IMAGE_INSTRUCTION=$(cat <<EOF
View-image treatment is enabled for this pilot. Use \`view_image\` to inspect
the generated setup/formal preview PNGs during setup/initial modeling, for
each Repair Hypothesis parent-child comparison (parent/child comparison), and
for final selection,
alongside objective measurements. Do not disable or avoid the \`view_image\`
tool.
EOF
    )
else
    VIEW_IMAGE_INSTRUCTION=$(cat <<EOF
View-image control mode is active: \`view_image\` is disabled; do not call \`view_image\`.
Use the formal preview JSON and objective measurements for
decisions while keeping all other pilot behavior unchanged.
EOF
    )
fi

# Minimal orchestrator prompt — peer skills, references, and commit
# conventions live in skills/mesh-to-cad/SKILL.md; duplicating them here
# would drift.
if [[ "$PLUGIN_MODE" == "direct" ]]; then
    PROMPT_PREAMBLE=$(cat <<'EOF'
You are the $mesh-to-cad skill orchestrator. Follow
skills/mesh-to-cad/SKILL.md verbatim; it is the authoritative contract.
EOF
    )
else
    PROMPT_PREAMBLE=$(cat <<'EOF'
Convert the provided Toys4K mesh into an editable parametric CAD model.
Inspect the mesh, reconstruct the object, validate the result, and produce
the canonical Workspace and its atomically published Final Delivery.
EOF
    )
fi

PROMPT=$(cat <<EOF
${PROMPT_PREAMBLE}

Input mesh: ${PLY}
Experiment directory (already an initialized local git repo, write ALL
artifacts here): ${EXP_DIR}

The canonical Workspace and its atomically published Final Delivery define
success. Optional additional human review material belongs under
${EXP_DIR}/reviews/ and never substitutes for formal Measured Step or Final
Delivery previews.
${RECONSTRUCTION_SPEC_INSTRUCTION}
${VIEW_IMAGE_INSTRUCTION}

Stay under ${EXP_DIR}; do not modify skills/, packages/, or files outside.
EOF
)

echo "[pilot] $OBJ → $EXP_DIR"

mkdir -p "$EXP_DIR/run"
printf '%s' "$PROMPT" > "${EXP_DIR}/run/prompt.txt"
printf '%s\n' "$PLUGIN_MODE" > "${EXP_DIR}/run/plugin-mode.txt"

WORKLOAD=(
    "gateway/codex-tap-gpt56"
    "$MODEL_SELECTOR"
    exec
    --skip-git-repo-check
)
if [[ "$PLUGIN_MODE" == "direct" ]]; then
    WORKLOAD+=(--disable plugins)
fi
WORKLOAD+=(
    -s
    danger-full-access
    "$PROMPT"
)

PILOT_EXIT=0
PYTHONPATH="$REPO_ROOT/packages/browser_runtime/src${PYTHONPATH:+:$PYTHONPATH}" \
"$PYTHON_BIN" "$SCRIPT_DIR/runner.py" run --input "$PLY" "$EXP_DIR" -- \
    "${WORKLOAD[@]}" < /dev/null > /dev/null \
    2> "${EXP_DIR}/run/stderr.log" || PILOT_EXIT=$?

if [[ $PILOT_EXIT -ne 0 ]]; then
    exit "$PILOT_EXIT"
fi

echo "[pilot] Done. Audit: /pilot-review $EXP_DIR/"
