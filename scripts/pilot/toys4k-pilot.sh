#!/usr/bin/env bash
# scripts/pilot/toys4k-pilot.sh <object_name> <group> [exp_name] [direct|e2e] [--view-image|--no-view-image] [--reconstruction-spec|--no-reconstruction-spec]
# MODEL selects the Venus gateway variant; it defaults to the public gpt-5.5 slug.
# Toys4K mesh-to-CAD benchmark pilot. Reads models/toys4k/<name>.ply,
# writes outputs/<group>/<TS>-<name>/. Group format: YYYYMMDD-HHMMSS-<slug>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# Production checkouts ship the trusted bridge and use the authority-hidden
# Agent Surface by default. Small launcher fixtures that copy only this script
# retain the legacy prompt; operators can force that compatibility path with
# AGENT_SURFACE_MODE=0.
if [[ "${AGENT_SURFACE_MODE:-}" == "0" ]]; then
    AGENT_SURFACE_MODE=0
elif [[ -f "$SCRIPT_DIR/agent_surface_bridge.py" ]]; then
    AGENT_SURFACE_MODE=1
else
    AGENT_SURFACE_MODE=0
fi
if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ "$AGENT_SURFACE_MODE" == "1" ]]; then
        PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
    else
        PYTHON_BIN=python3
    fi
fi

# Defensive: source secrets so nohup toys4k-pilot.sh works without
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
if [[ "$AGENT_SURFACE_MODE" == 1 ]]; then
    if [[ "$RECONSTRUCTION_SPEC" == 1 ]]; then
        RECONSTRUCTION_SPEC_INSTRUCTION=$(cat <<'EOF'
Reconstruction Spec is enabled. Keep the mutable document at the fixed
candidate mount path /candidate/reconstruction-spec.json. Do not inspect or
name any host, Workspace, input, or persistence path.
EOF
        )
    else
        RECONSTRUCTION_SPEC_INSTRUCTION=$(cat <<'EOF'
Reconstruction Spec is disabled for this run. Do not create, read, or update a
Reconstruction Spec.
EOF
        )
    fi
elif [[ "$RECONSTRUCTION_SPEC" == 1 ]]; then
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
View-image treatment is enabled for this pilot. Use view_image to inspect
the generated setup/formal preview PNGs during setup/initial modeling, for
each Repair Hypothesis parent-child comparison (parent/child comparison), and
for final selection,
alongside objective measurements. Do not disable or avoid the view_image
tool.
EOF
    )
else
    VIEW_IMAGE_INSTRUCTION=$(cat <<EOF
View-image control mode is active: view_image is disabled; do not call view_image.
Use the formal preview JSON and objective measurements for
decisions while keeping all other pilot behavior unchanged.
EOF
    )
fi

# Minimal orchestrator prompt — peer skills, references, and commit
# conventions live in skills/mesh-to-cad/SKILL.md; duplicating them here
# would drift.
if [[ "$PLUGIN_MODE" == "direct" || "$AGENT_SURFACE_MODE" == 1 ]]; then
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

if [[ "$AGENT_SURFACE_MODE" == 1 ]]; then
    PROMPT=$(cat <<EOF
${PROMPT_PREAMBLE}

This is an authority-hidden Agent Surface execution. Read the opaque bootstrap
contract from the fixed candidate mount /candidate/bootstrap.json. Use only
ordinary exec_command calls to run the fixed client
python3 /agent-surface/client.py for these seven JSON intents:
workspace_status, start_attempt, run_candidate_tool,
submit_step_zero, submit_repair, select_and_finalize, and observe_reference.
For each call, provide exactly one closed JSON request envelope on stdin:
{"schema":"mesh-to-cad.agent-intent/1","intent":"<intent>","args":{...}}.
For observe_reference, args must be exactly
{"reference_handle":"<opaque>","observation":{"method":"summary","args":{}}} or
{"reference_handle":"<opaque>","observation":{"method":"section_profile","args":{}}}.
Read the one JSON client response before issuing another request; requests are
strictly serial. A publication can outlive an exec_command yield: normally poll
that same session_id through write_stdin until its response arrives. If a
completed publication response is unavailable, call workspace_status; its
publication_recovery, when present, is the exact published response and must
be used without resubmitting W1. Do not issue another intent while a client
session is live.
On an error, preserve its classification and do not retry blindly. For a closed
repair_evidence_failed response, use only its subtype to choose a permitted next
intent; never request host diagnostics. After each published Step response with a preview_handle, emit no text before calling the only MCP
tool you may use: in code mode its callable ID is
mcp__agent_surface__inspect_formal_preview; native Responses uses namespace
mcp__agent_surface with child inspect_formal_preview. Never use a dotted
server/tool spelling. Inspect its image block before creating or updating the
Reconstruction Spec. Do not use tool_search, inspect paths, or invoke another
client.
Use only handles from the bootstrap contract and the returned
capability_bundle_handle; reuse that attempt-scoped bundle for candidate tool
and evidence submissions. Never invent paths, argv, shell
commands, Workspace IDs, reference IDs, or persistence locations. Write model
source and candidate evidence only below /candidate. The trusted supervisor
owns Workspace publication, Git/LFS, validation, recovery, and the terminal
handoff. Raw and canonical reference bytes are unavailable to this process.

The canonical Workspace and its atomically published Final Delivery define
success. ${RECONSTRUCTION_SPEC_INSTRUCTION}

After every published Step response with an opaque preview_handle, emit no
text before calling the Agent Surface MCP inspection tool: code mode uses
mcp__agent_surface__inspect_formal_preview; native Responses uses namespace
mcp__agent_surface with child inspect_formal_preview. Never use a dotted
server/tool spelling. Inspect the returned image block before creating or
updating the Reconstruction Spec. Do not use view_image, paths, URLs, or
host/capability discovery.
EOF
    )
else
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
fi

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
if [[ "$PLUGIN_MODE" == "direct" && "$AGENT_SURFACE_MODE" != 1 ]]; then
    WORKLOAD+=(--disable plugins)
fi
WORKLOAD+=(
    -s
    danger-full-access
    "$PROMPT"
)

PILOT_EXIT=0
if [[ "$AGENT_SURFACE_MODE" == 1 ]]; then
    RUNNER_RECONSTRUCTION_SPEC_ARGS=()
    if [[ "$RECONSTRUCTION_SPEC" == 1 ]]; then
        RUNNER_RECONSTRUCTION_SPEC_ARGS+=("--reconstruction-spec")
    fi
    PYTHONPATH="$REPO_ROOT/packages/browser_runtime/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$SCRIPT_DIR/runner.py" run --agent-surface \
        "${RUNNER_RECONSTRUCTION_SPEC_ARGS[@]}" --input "$PLY" "$EXP_DIR" -- \
        "${WORKLOAD[@]}" < /dev/null > /dev/null \
        2> "${EXP_DIR}/run/stderr.log" || PILOT_EXIT=$?
else
    # Legacy compatibility syntax retained for controlled non-bridge runs.
    PYTHONPATH="$REPO_ROOT/packages/browser_runtime/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$SCRIPT_DIR/runner.py" run --input "$PLY" "$EXP_DIR" -- \
        "${WORKLOAD[@]}" < /dev/null > /dev/null \
        2> "${EXP_DIR}/run/stderr.log" || PILOT_EXIT=$?
fi

if [[ $PILOT_EXIT -ne 0 ]]; then
    exit "$PILOT_EXIT"
fi

echo "[pilot] Done. Audit: /pilot-review $EXP_DIR/"
