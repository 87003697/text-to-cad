#!/usr/bin/env bash
#
# sandbox-clean.sh EXP_DIR — remove the overlay directories that
# sandbox-init.sh created. Idempotent: safe on missing paths, safe to
# run twice.
#
# Standalone use case: after a pilot failure that preserved the sandbox
# for postmortem, run this once you're done inspecting to reclaim disk.
# codex-exit.sh calls this internally on successful pilots.

set -euo pipefail

EXP_DIR="${1:?Usage: sandbox-clean.sh EXP_DIR}"
rm -rf "$EXP_DIR/.codex-upper" "$EXP_DIR/.codex-work"
