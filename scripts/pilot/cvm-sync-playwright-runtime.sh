#!/usr/bin/env bash
# Stable, input-free entrypoint for the exact provider-free Python dependency.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
exec python3 -m scripts.pilot.cvm_playwright_runtime_sync "$@"
