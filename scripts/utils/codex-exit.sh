#!/usr/bin/env bash
#
# codex-exit.sh EXP_DIR CODEX_EXIT — post-run teardown for a codex pilot.
#
# Layer role
# ----------
# Paired with codex-init.sh. Does the two codex-specific things that
# happen AFTER `$CODEX_RUN "$PROMPT"` returns:
#   1. Extract the rollout captured by the sandbox to $EXP_DIR/rollout.jsonl
#   2. Clean up the sandbox — or preserve it for postmortem based on the
#      exit code and the KEEP_STATE env var.
#
# Usage
# -----
#   codex-exit.sh EXP_DIR CODEX_EXIT
#     where CODEX_EXIT is the exit code $CODEX_RUN produced.
#
# Environment
# -----------
#   SANDBOX_UPPER  required — set by codex-init.sh / sandbox-init.sh in the
#                  caller's shell via eval.
#   KEEP_STATE     optional — any non-empty value keeps the overlay dirs
#                  even on a successful run (default: they're cleaned).
#                  Failures ALWAYS preserve regardless of this.
#
# Exit codes
# ----------
#   0    rollout captured, cleanup applied per policy
#   3    rollout extraction anomaly — 0 or 2+ rollouts under SANDBOX_UPPER.
#        Sandbox is preserved for postmortem.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXP_DIR="${1:?Usage: codex-exit.sh EXP_DIR CODEX_EXIT}"
CODEX_EXIT="${2:?Usage: codex-exit.sh EXP_DIR CODEX_EXIT}"
: "${SANDBOX_UPPER:?SANDBOX_UPPER not set; call 'eval \"\$(codex-init.sh EXP_DIR)\"' first}"

# 1. Extract the rollout. Because a fresh per-pilot overlay was used,
#    SANDBOX_UPPER/sessions/ holds exactly one rollout. Anything else is
#    a real anomaly:
#      0 rollouts → codex crashed before writing session_meta, or the
#                   overlay didn't attach.
#      2+ rollouts → isolation broke (shouldn't be possible).
#    Either way: exit 3 and preserve the sandbox for forensics.
ROLLOUTS=("$SANDBOX_UPPER"/sessions/*/*/*/rollout-*.jsonl)
if [[ ${#ROLLOUTS[@]} -ne 1 || ! -f "${ROLLOUTS[0]}" ]]; then
    echo "expected exactly 1 rollout under $SANDBOX_UPPER, found ${#ROLLOUTS[@]}" >&2
    echo "sandbox preserved for postmortem" >&2
    echo "clean when done: $SCRIPT_DIR/sandbox-clean.sh \"$EXP_DIR\"" >&2
    exit 3
fi
mv "${ROLLOUTS[0]}" "$EXP_DIR/rollout.jsonl"

# 2. Cleanup policy:
#    codex succeeded (exit 0) + no KEEP_STATE   → clean the sandbox
#    codex failed (any non-zero exit)           → preserve (postmortem)
#    KEEP_STATE non-empty (any exit)            → preserve (dev opt-in)
if [[ $CODEX_EXIT -eq 0 && -z "${KEEP_STATE:-}" ]]; then
    "$SCRIPT_DIR/sandbox-clean.sh" "$EXP_DIR"
else
    echo "sandbox preserved at $SANDBOX_UPPER (exit=$CODEX_EXIT)" >&2
    echo "clean when done: $SCRIPT_DIR/sandbox-clean.sh \"$EXP_DIR\"" >&2
fi
