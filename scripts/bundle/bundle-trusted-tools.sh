#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANIFEST_TOOL="$REPO_ROOT/scripts/pilot/trusted_tools.py"

if [ "${1:-}" = "--check" ]; then
  python3 "$MANIFEST_TOOL" --repo-root "$REPO_ROOT" --check
elif [ "$#" -eq 0 ]; then
  python3 "$MANIFEST_TOOL" --repo-root "$REPO_ROOT"
else
  echo "Usage: scripts/bundle/bundle-trusted-tools.sh [--check]" >&2
  exit 2
fi
