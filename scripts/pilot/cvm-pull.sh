#!/usr/bin/env bash
# Stable entrypoint for the CVM -> S3 pull/publish workflow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/cvm_pull.py" "$@"
