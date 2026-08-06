#!/usr/bin/env bash
# Stable entrypoint for the Mac -> CVM production deployment workflow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/cvm_push.py" "$@"
